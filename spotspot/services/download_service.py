import sys
import os
import logging
import subprocess
import stat

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class DownloadService:
    def __init__(self, config, playlist_manager, socketio, download_queue, download_history):
        self.config = config
        self.playlist_manager = playlist_manager
        self.socketio = socketio
        self.download_queue = download_queue
        self.download_history = download_history

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

    def ensure_writable_directory(self, path):
        if not os.access(path, os.W_OK):
            logging.warning(f"Directory {path} is not writable. Attempting to fix permissions.")
            try:
                os.chmod(path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                logging.info(f"Permissions for {path} set to 777.")
            except Exception as e:
                logging.error(f"Failed to set permissions on {path}: {e}")

    def process_downloads(self):
        while True:
            url, download_info = self.download_queue.get()

            if download_info.get("status") == "Cancelled":
                self.download_queue.task_done()
                continue

            try:
                download_path = self.config.track_output.format_map(download_info)
            except Exception:
                download_path = self.config.track_output

            os.makedirs(download_path, exist_ok=True)
            self.ensure_writable_directory(download_path)

            download_info["status"] = "Downloading..."
            self.download_history[url] = download_info
            self.socketio.emit("update_status", {"history": list(self.download_history.values())})

            try:
                logging.info(f"Downloading: {url}")

                if download_info.get("type") == "playlist":
                    playlist_name = (
                        download_info.get("name", "playlist")
                        .strip()
                        .replace("/", "-")
                        + ".m3u"
                    )
                    command = [
                        "spotdl",
                        "--output", ".",
                        "--m3u", playlist_name,
                        "--log-level", "DEBUG",
                        "--print-errors",
                        url
                    ]
                else:
                    command = ["spotdl", "--output", ".", url]

                logging.info(f"SpotDL command: {command} (cwd={download_path})")

                proc = subprocess.Popen(
                    command,
                    cwd=download_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )

                for line in proc.stdout:
                    logging.info(line.rstrip())

                returncode = proc.wait()
                if returncode != 0:
                    logging.error(f"SpotDL exit code {returncode}")
                    download_info["status"] = "Failed"
                else:
                    download_info["status"] = "Complete"

                self.download_history[url] = download_info

            except Exception as e:
                logging.error(f"Process Downloads Error: {e}")
                download_info["status"] = "Error"
                self.download_history[url] = download_info

            self.socketio.emit("update_status", {"history": list(self.download_history.values())})
            self.download_queue.task_done()
