import os
import uuid
import threading
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
import yt_dlp

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_prefix=1)

DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "downloads")).resolve()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

COOKIES_FILE = Path(os.environ.get("COOKIES_FILE", "cookies.txt")).resolve()


def _base_ydl_opts() -> dict:
    """Common yt-dlp options shared across all calls."""
    opts: dict = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    return opts

# In-memory task store: task_id -> {status, progress, filename, error, title}
tasks: dict[str, dict] = {}


class ProgressHook:
    def __init__(self, task_id: str):
        self.task_id = task_id

    def __call__(self, d: dict):
        task = tasks[self.task_id]
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                task["progress"] = round(downloaded / total * 100, 1)
            task["status"] = "downloading"
        elif d["status"] == "finished":
            task["status"] = "processing"
            task["progress"] = 100


def _download(task_id: str, url: str, format_id: str, audio_only: bool):
    task = tasks[task_id]
    try:
        outtmpl = str(DOWNLOAD_DIR / f"{task_id}_%(title)s.%(ext)s")

        ydl_opts = _base_ydl_opts()
        ydl_opts.update({
            "outtmpl": outtmpl,
            "progress_hooks": [ProgressHook(task_id)],
        })

        if audio_only:
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        elif format_id:
            ydl_opts["format"] = format_id
        else:
            ydl_opts["format"] = "bestvideo+bestaudio/best"
            ydl_opts["merge_output_format"] = "mp4"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")
            task["title"] = title

            # Find the downloaded file
            filename = ydl.prepare_filename(info)
            if audio_only:
                filename = Path(filename).with_suffix(".mp3")
            filename = Path(filename)

            # yt-dlp may change extension, find the actual file
            if not filename.exists():
                pattern = f"{task_id}_*"
                matches = list(DOWNLOAD_DIR.glob(pattern))
                if matches:
                    filename = matches[0]

            task["filename"] = filename.name
            task["status"] = "done"
            task["progress"] = 100

    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def video_info():
    """Fetch video metadata and available formats."""
    url = request.json.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    try:
        ydl_opts = _base_ydl_opts()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []
        seen_res = set()
        for f in info.get("formats", []):
            if f.get("vcodec", "none") == "none":
                continue
            res = f.get("height", 0)
            if res and res not in seen_res:
                seen_res.add(res)
                filesize = f.get("filesize") or f.get("filesize_approx")
                formats.append(
                    {
                        "format_id": f"bestvideo[height<={res}]+bestaudio/best[height<={res}]",
                        "ext": "mp4",
                        "resolution": f"{res}p",
                        "filesize": filesize,
                        "note": f.get("format_note", ""),
                    }
                )
        formats.sort(key=lambda x: int(x["resolution"].replace("p", "")), reverse=True)

        return jsonify(
            {
                "title": info.get("title"),
                "thumbnail": info.get("thumbnail"),
                "duration": info.get("duration"),
                "uploader": info.get("uploader"),
                "formats": formats,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
def download():
    """Start a download task."""
    url = request.json.get("url", "").strip()
    format_id = request.json.get("format_id", "")
    audio_only = request.json.get("audio_only", False)

    if not url:
        return jsonify({"error": "URL is required"}), 400

    task_id = uuid.uuid4().hex[:12]
    tasks[task_id] = {"status": "starting", "progress": 0, "filename": None, "error": None, "title": None}

    thread = threading.Thread(target=_download, args=(task_id, url, format_id, audio_only))
    thread.daemon = True
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/api/status/<task_id>")
def status(task_id):
    """Check download progress."""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@app.route("/api/file/<task_id>")
def get_file(task_id):
    """Download the completed file."""
    task = tasks.get(task_id)
    if not task or task["status"] != "done":
        return jsonify({"error": "File not ready"}), 404
    return send_from_directory(DOWNLOAD_DIR, task["filename"], as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
