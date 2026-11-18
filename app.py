import gradio as gr
import google.generativeai as genai
import subprocess, json, time, os, math

API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=API_KEY)

# Render Environment থেকে কুকিজ নিয়ে ফাইল তৈরি করা (JSON বা Netscape যেকোনো ফরম্যাট সাপোর্ট করবে)
YT_COOKIES_DATA = os.environ.get("YT_COOKIES", "").strip()
if YT_COOKIES_DATA:
    if YT_COOKIES_DATA.startswith("["):
        try:
            cookies_list = json.loads(YT_COOKIES_DATA)
            with open("cookies.txt", "w", encoding="utf-8") as f:
                f.write("# Netscape HTTP Cookie File\n")
                for c in cookies_list:
                    domain = c.get("domain", "")
                    flag = "TRUE" if domain.startswith(".") else "FALSE"
                    path = c.get("path", "/")
                    secure = "TRUE" if c.get("secure", False) else "FALSE"
                    exp = str(int(c.get("expirationDate", 1893456000)))
                    name = c.get("name", "")
                    val = c.get("value", "")
                    f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{exp}\t{name}\t{val}\n")
        except Exception as e:
            print("Cookie JSON parse error:", e)
    else:
        with open("cookies.txt", "w", encoding="utf-8") as f:
            f.write(YT_COOKIES_DATA)
    YT_BYPASS = ["--cookies", "cookies.txt", "--extractor-args", "youtube:player_client=web,android"]
else:
    YT_BYPASS = ["--extractor-args", "youtube:player_client=android,ios"]

crop_map = {
    "top_left": "480:360:30:30",
    "top_right": "480:360:1410:30",
    "bottom_left": "480:360:30:690",
    "bottom_right": "480:360:1410:690"
}

def format_srt_time(seconds):
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hrs:02}:{mins:02}:{secs:02},{millis:03}"

def sec_to_hms(seconds):
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hrs:02}:{mins:02}:{secs:02}"

def process_livestream(video_url, clip_count, progress=gr.Progress()):
    try:
        if not video_url:
            return None, "❌ দয়া করে একটি সঠিক লাইভস্ট্রিম বা ভিডিও লিংক দিন!"

        progress(0.05, desc="১. লাইভস্ট্রিমের তথ্য যাচাই করা হচ্ছে...")
        
        res = subprocess.run(
            ["yt-dlp"] + YT_BYPASS + ["--get-duration", video_url],
            capture_output=True, text=True
        )
        if res.returncode != 0 or not res.stdout.strip():
            return None, f"❌ ইউটিউব লিংকটি ব্লক করেছে বা ভুল লিংক:\n{res.stderr[:300]}"

        dur_str = res.stdout.strip().split('\n')[0]
        parts = [int(p) for p in dur_str.split(':')]
        if len(parts) == 3:
            total_seconds = parts[0]*3600 + parts[1]*60 + parts[2]
        elif len(parts) == 2:
            total_seconds = parts[0]*60 + parts[1]
        else:
            total_seconds = parts[0]

        chunk_size = 2400 
        num_chunks = max(1, math.ceil(total_seconds / chunk_size))
        clips_per_chunk = max(1, math.ceil(int(clip_count) / num_chunks))
        
        all_highlights = []
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")

        for c in range(num_chunks):
            if len(all_highlights) >= int(clip_count):
                break
                
            chunk_start = c * chunk_size
            chunk_end = min((c + 1) * chunk_size, total_seconds)
            
            progress(0.1 + (0.4 * (c / num_chunks)), desc=f"২. অংশ {c+1}/{num_chunks} ({sec_to_hms(chunk_start)} - {sec_to_hms(chunk_end)}) বিশ্লেষণ হচ্ছে...")
            
            low_res_file = f"chunk_{c}.mp4"
            if os.path.exists(low_res_file):
                os.remove(low_res_file)

            subprocess.run(
                ["yt-dlp"] + YT_BYPASS + [
                    "-f", "worstvideo[height>=360][ext=mp4]+worstaudio[ext=m4a]/worst[ext=mp4]",
                    "--download-sections", f"*{sec_to_hms(chunk_start)}-{sec_to_hms(chunk_end)}",
                    "-o", low_res_file, video_url
                ], capture_output=True, text=True
            )

            if not os.path.exists(low_res_file):
                continue

            video_file = genai.upload_file(path=low_res_file)
            while video_file.state.name == "PROCESSING":
                time.sleep(5)
                video_file = genai.get_file(video_file.name)

            prompt = f"""
            Analyze this livestream segment carefully. Find the top {clips_per_chunk} most viral, funny, or intense moments (each 20 to 50 seconds long).
            For each moment determine:
            1. "start": start time in seconds relative to this segment.
            2. "end": end time in seconds relative to this segment.
            3. "layout": "split" (streamer facecam in a corner + gameplay/screen) OR "full_face" (streamer face fills whole screen).
            4. "face_pos": If "split", choose from: "top_left", "top_right", "bottom_left", "bottom_right". If "full_face", put "center".
            5. "captions": Transcribe spoken dialogue with relative timestamps from clip start (0.0s). English in English, Hindi strictly in Hinglish (Roman script). Short 3-5 word chunks.

            Return ONLY valid JSON:
            [{{"start": 45.0, "end": 80.0, "layout": "split", "face_pos": "bottom_left", "captions": [{{"start": 0.5, "end": 2.5, "text": "Bhai ye kya scene hai"}}]}}]
            """
            
            response = model.generate_content([video_file, prompt])
            clean_json = response.text.replace("```json", "").replace("```", "").strip()
            chunk_highlights = json.loads(clean_json)
            
            for item in chunk_highlights:
                item['abs_start'] = chunk_start + item['start']
                item['abs_end'] = chunk_start + item['end']
                all_highlights.append(item)

            genai.delete_file(video_file.name)
            if os.path.exists(low_res_file):
                os.remove(low_res_file)

        if not all_highlights:
            return None, "❌ ভিডিওটি ডাউনলোড বা প্রসেস করা সম্ভব হয়নি।"

        all_highlights = all_highlights[:int(clip_count)]
        output_videos = []

        for i, item in enumerate(all_highlights):
            progress(0.55 + (0.4 * (i / len(all_highlights))), desc=f"৩. ক্লিপ {i+1} সরাসরি 1080p তে ডাউনলোড ও এডিট হচ্ছে...")
            
            abs_start = item['abs_start']
            abs_end = item['abs_end']
            layout = item['layout']
            face_pos = item.get('face_pos', 'bottom_left')
            
            raw_1080p = f"raw_1080p_{i}.mp4"
            srt_file = f"sub_{i}.srt"
            final_out = f"Viral_Stream_Clip_{i+1}_1080p.mp4"

            for f_name in [raw_1080p, srt_file, final_out]:
                if os.path.exists(f_name):
                    os.remove(f_name)

            subprocess.run(
                ["yt-dlp"] + YT_BYPASS + [
                    "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
                    "--download-sections", f"*{sec_to_hms(abs_start)}-{sec_to_hms(abs_end)}",
                    "-o", raw_1080p, video_url
                ], capture_output=True, text=True
            )

            with open(srt_file, "w", encoding="utf-8") as f:
                for idx, cap in enumerate(item['captions']):
                    f.write(f"{idx+1}\n{format_srt_time(cap['start'])} --> {format_srt_time(cap['end'])}\n{cap['text']}\n\n")

            if layout == "split" and face_pos in crop_map:
                f_crop = crop_map[face_pos]
                filter_complex = (
                    f"[0:v]crop={f_crop},scale=1080:640:force_original_aspect_ratio=increase,crop=1080:640[top];"
                    f"[0:v]crop=1080:1080:420:0,scale=1080:1280[bottom];"
                    f"[top][bottom]vstack=inputs=2[stacked];"
                    f"[stacked]subtitles={srt_file}:force_style='Alignment=10,Fontsize=20,Bold=1,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2'[outv]"
                )
            else:
                filter_complex = (
                    f"[0:v]crop=608:1080:656:0,scale=1080:1920[full];"
                    f"[full]subtitles={srt_file}:force_style='Alignment=2,MarginV=120,Fontsize=20,Bold=1,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2'[outv]"
                )

            subprocess.run([
                "ffmpeg", "-y", "-i", raw_1080p, "-filter_complex", filter_complex,
                "-map", "[outv]", "-map", "0:a", "-c:v", "libx264", "-crf", "18", 
                "-preset", "fast", "-c:a", "aac", final_out
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if os.path.exists(raw_1080p):
                os.remove(raw_1080p)
                
            output_videos.append(final_out)

        progress(1.0, desc="সবগুলো 1080p ক্লিপ তৈরি সম্পন্ন!")
        return output_videos, "✅ সফলভাবে সবগুলো 1080p ক্লিপ তৈরি হয়েছে!"
    except Exception as e:
        return None, f"❌ সিস্টেম এরর: {str(e)}"

with gr.Blocks(theme=gr.themes.Soft()) as app:
    gr.Markdown("# 🎬 AI Livestream Viral Shorts Maker v3.0 (1080p)")
    gr.Markdown("৩-৪ ঘণ্টার বড় লাইভস্ট্রিম বা ভিডিওর লিংক দিন। সিস্টেম নিজে থেকেই সেরা মুহূর্তগুলো 1080p কোয়ালিটিতে তৈরি করে দেবে।")
    
    with gr.Row():
        url_input = gr.Textbox(label="লাইভস্ট্রিম বা ভিডিও লিংক", placeholder="https://youtube.com...")
        clip_slider = gr.Slider(minimum=1, maximum=6, value=3, step=1, label="কয়টি ক্লিপ চান?")
        
    submit_btn = gr.Button("🚀 ক্লিপ তৈরি শুরু করুন", variant="primary")
    status_box = gr.Textbox(label="স্ট্যাটাস / মেসেজ", interactive=False)
    output_files = gr.File(label="তৈরি হওয়া 1080p ভিডিওগুলো (ডাউনলোড করতে ক্লিক করুন)")
    
    submit_btn.click(
        fn=process_livestream,
        inputs=[url_input, clip_slider],
