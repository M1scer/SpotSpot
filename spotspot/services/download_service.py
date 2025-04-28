import sys
import os
import shutil
import logging
import subprocess

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class DownloadService:
    def __init__(self, config, playlist_manager, socketio, download_queue, download_history):
        self.config = config
        self.playlist_manager = playlist_manager
        self.socketio = socketio
        self.download_queue = download_queue
        self.download_history = download_history
        self.spotdl_subprocess = None

    def add_item_to_queue(self, data):
        logging.info(f"Download Requested: {data}")
        spotify_url = data.get("url")
        item_type = data.get("type")
        item_name = data.get("name")
        item_artist = data.get("artist")

        download_info = {
            "url": spotify_url,
            "type": item_type,
            "name": item_name,
            "artist": item_artist,
            "status": "Pending..."
        }

        self.download_queue.put((spotify_url, download_info))
        self.download_history[spotify_url] = download_info
        self.socketio.emit("update_status", {"history": list(self.download_history.values())})

    def process_downloads(self):
        while True:
            url, download_info = self.download_queue.get()

            if download_info["status"] == "Cancelled":
                self.download_queue.task_done()
                continue

            # Determine download path for audio files using placeholders
            try:
                download_path = self.config.track_output.format_map(download_info)
            except Exception:
                download_path = self.config.track_output

            os.makedirs(download_path, exist_ok=True)
            download_info["status"] = "Downloading..."
            self.download_history[url] = download_info
            self.socketio.emit("update_status", {"history": list(self.download_history.values())})

            try:
                logging.info(f"Downloading: {url}")

                # Build SpotDL command; include --m3u only for playlists
                if download_info["type"] == "playlist":
                    # sanitize playlist name
                    safe_name = download_info.get("name", "playlist").strip().replace("/", "-") + ".m3u"
                    command = [
                        "spotdl",
                        "--output", download_path,
                        "--m3u", safe_name,
                        url
                    ]
                else:
                    command = ["spotdl", "--output", download_path, url]

                logging.info(f"SpotDL command: {command}")
                proc = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                stdout, stderr = proc.communicate()

                if proc.returncode != 0:
                    download_info["status"] = "Failed"
                    logging.error(f"Error downloading: {stderr.strip()}")
                else:
                    download_info["status"] = "Complete"
                    logging.info("Finished Item")
                self.download_history[url] = download_info

                # Handle M3U file for playlists
                if download_info["type"] == "playlist":
                    m3u_name = f"{safe_name}.m3u8"
                    src = os.path.join(download_path, m3u_name)
                    dest_dir = self.config.m3u_playlist_path
                    os.makedirs(dest_dir, exist_ok=True)
                    if os.path.isfile(src):
                        dest = os.path.join(dest_dir, m3u_name)
                        shutil.move(src, dest)
                        logging.info(f"M3U moved to: {dest}")
                        # Convert relative entries to absolute
                        with open(dest, 'r+', encoding='utf-8') as f:
                            lines = f.readlines()
                            f.seek(0)
                            for entry in lines:
                                rel = entry.strip()
                                abs_path = os.path.abspath(os.path.join(download_path, rel))
                                f.write(abs_path + "\n")
                            f.truncate()
                        logging.info("M3U entries converted to absolute paths")
                    else:
                        logging.warning(f"M3U file not found at: {src}")

            except Exception as e:
                logging.error(f"Process Downloads Error: {e}")
                download_info["status"] = "Error"
                self.download_history[url] = download_info

            self.socketio.emit("update_status", {"history": list(self.download_history.values())})
            self.download_queue.task_done()
