import os
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

    def add_item_to_queue(self, data):
        logging.info(f"Download Requested: {data}")
        spotify_url = data.get("url")
        download_info = {
            "url": spotify_url,
            "type": data.get("type"),
            "name": data.get("name"),
            "artist": data.get("artist"),
            "status": "Pending..."
        }
        self.download_queue.put((spotify_url, download_info))
        self.download_history[spotify_url] = download_info
        self.socketio.emit("update_status", {"history": list(self.download_history.values())})

    def process_downloads(self):
        # Ensure SpotDL config is written
        try:
            self.config.get_spotdl_vars()
            self.config.create_config_file()
        except Exception:
            logging.warning("Could not write SpotDL config file, proceeding with environment settings.")

        while True:
            url, download_info = self.download_queue.get()

            if download_info.get("status") == "Cancelled":
                self.download_queue.task_done()
                continue

            # Determine download path
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

                # Build SpotDL command; use cwd so output and m3u are in same folder
                if download_info.get("type") == "playlist":
                    playlist_name = (
                        download_info.get("name", "playlist").strip().replace("/", "-") + ".m3u"
                    )
                    command = [
                        "spotdl",
                        "--output", ".",
                        "--m3u", playlist_name,
                        "--log-level", self.config.log_level,
                        "--print-errors",
                        url
                    ]
                else:
                    command = [
                        "spotdl",
                        "--output", ".",
                        url
                    ]

                logging.info(f"SpotDL command: {command} (cwd={download_path})")
                proc = subprocess.Popen(
                    command,
                    cwd=download_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )

                # Live log output
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
