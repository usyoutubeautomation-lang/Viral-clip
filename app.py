import gradio as gr
import google.generativeai as genai
import subprocess, json, time, os

API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=API_KEY)

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

def process_video(video_url, clip_count, progress=gr.Progress()):
    if not video_url:
        raise gr.Error("দয়া করে একটি সঠিক ভিডিও লিংক দিন!")
        
    progress(0.1, desc="১. 1080p ভিডিও ডাউনলোড হচ্ছে...")
    if os.path.exists("input.mp4"):
        os.remove("input.mp4")
        
    subprocess.run([
        "yt-dlp", "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]", 
        "-o", "input.mp4", video_url
    ], check=True)

    progress(0.3, desc="২. AI ভিডিওটি দেখছে এবং সেরা মুহূর্ত খুঁজছে...")
    video_file = genai.upload_file(path="input.mp4")
    while video_file.state.name == "PROCESSING":
        time.sleep(5)
        video_file = genai.get_file(video_file.name)

    model = genai.GenerativeModel(model_name="gemini-2.5-flash")
    prompt = f"""
    Analyze this video carefully. Find the top {int(clip_count)} most viral, funny, or intense moments (each 20 to 50 seconds long).
    For each moment determine:
    1. "start": exact start time in seconds.
    2. "end": exact end time in seconds.
    3. "layout": "split" (streamer facecam in a corner + gameplay/screen) OR "full_face" (streamer face fills whole screen).
    4. "face_pos": If "split", choose from: "top_left", "top_right", "bottom_left", "bottom_right". If "full_face", put "center".
    5. "captions": Transcribe spoken dialogue with relative timestamps from clip start (0.0s). English in English, Hindi strictly in Hinglish (Roman script). Short 3-5 word chunks.

    Return ONLY valid JSON:
    [{{"start": 45.0, "end": 80.0, "layout": "split", "face_pos": "bottom_left", "captions": [{{"start": 0.5, "end": 2.5, "text": "Bhai ye kya scene hai"}}]}}]
    """
    
    response = model.generate_content([video_file, prompt])
    clean_json = response.text.replace("```json", "").replace("```", "").strip()
    highlights = json.loads(clean_json)

    output_videos = []
    
    for i, item in enumerate(highlights):
        progress(0.5 + (0.4 * (i / len(highlights))), desc=f"৩. ক্লিপ {i+1} এডিট এবং 1080p রেন্ডার হচ্ছে...")
        start_t = item['start']
        duration = item['end'] - start_t
        layout = item['layout']
        face_pos = item.get('face_pos', 'bottom_left')
        
        raw_clip = f"raw_{i}.mp4"
        srt_file = f"sub_{i}.srt"
        final_out = f"Viral_Clip_{i+1}_1080p.mp4"

        subprocess.run([
            "ffmpeg", "-y", "-ss", str(start_t), "-t", str(duration), 
            "-i", "input.mp4", "-c:v", "libx264", "-crf", "18", "-c:a", "aac", raw_clip
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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
            "ffmpeg", "-y", "-i", raw_clip, "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "0:a", "-c:v", "libx264", "-crf", "18", 
            "-preset", "fast", "-c:a", "aac", final_out
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        output_videos.append(final_out)

    progress(1.0, desc="সব কাজ সম্পন্ন!")
    return output_videos

with gr.Blocks(theme=gr.themes.Soft()) as app:
    gr.Markdown("# 🎬 AI Auto Viral Shorts Maker (1080p)")
    gr.Markdown("যেকোনো ভিডিও বা লাইভস্ট্রিমের লিংক দিন, সিস্টেম নিজে থেকেই সেরা মুহূর্তগুলো তৈরি করে দেবে।")
    
    with gr.Row():
        url_input = gr.Textbox(label="ভিডিও বা লাইভস্ট্রিম লিংক", placeholder="https://youtube.com...")
        clip_slider = gr.Slider(minimum=1, maximum=6, value=3, step=1, label="কয়টি ক্লিপ চান?")
        
    submit_btn = gr.Button("🚀 ক্লিপ তৈরি শুরু করুন", variant="primary")
    output_files = gr.File(label="তৈরি হওয়া 1080p ভিডিওগুলো")
    
    submit_btn.click(
        fn=process_video,
        inputs=[url_input, clip_slider],
        outputs=output_files
    )

app.launch(server_name="0.0.0.0", server_port=7860)
