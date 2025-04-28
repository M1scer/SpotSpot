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
        self.spodtdl_subprocess = None

    def add_item_to_queue(self, data):
        logging.info(f"Download Requested: {data}")

        spotify_url = data.get("url")
        item_type = data.get("type")
        item_name = data.get("name")
        item_artist = data.get("artist")

        download_info = {
            "name": item_name,
            "type": item_type,
            "artist": item_artist,
            "url": spotify_url,
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

            # Choose target folder based on type
            if download_info["type"] == "track":
                download_path = self.config.track_output
            elif download_info["type"] == "album":
                download_path = self.config.album_output
            elif download_info["type"] == "artist":
                download_path = self.config.artist_output
            else:
                # For playlist and unknown types, store tracks in normal track folder
                download_path = self.config.track_output
                download_path = self.config.track_output

            os.makedirs(download_path, exist_ok=True)
            download_info["status"] = "Downloading..."
            self.download_history[url] = download_info
            self.socketio.emit("update_status", {"history": list(self.download_history.values())})

            try:
                logging.info(f"Downloading: {url}")

                # Use SpotDL's built-in M3U only for playlists
                if download_info["type"] == "playlist":
                    command = [
                        "spotdl",
                        "--output", download_path,
                        "--m3u", safe_name,
                        url
                    ]
                else:
                    command = ["spotdl", "--output", download_path, url]

                logging.info(f"SpotDL command: {command}")
                self.spodtdl_subprocess = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )

                stdout, stderr = self.spodtdl_subprocess.communicate()

                if self.spodtdl_subprocess.returncode != 0:
                    download_info["status"] = "Failed"
                    logging.error(f"Error downloading: {stderr.strip()}")
                else:
                    download_info["status"] = "Complete"
                    logging.info("Finished Item")

                self.download_history[url] = download_info

                # Move and fix M3U for playlists
                if download_info["type"] == "playlist":
                    m3u_name = f"{safe_name}.m3u8"
                    # possible locations for m3u
                    candidates = [
                        os.path.join(download_path, m3u_name),
                        os.path.join(os.getcwd(), m3u_name)
                    ]
                    src = next((p for p in candidates if os.path.isfile(p)), None)
                    dest_dir = self.config.m3u_playlist_path
                    os.makedirs(dest_dir, exist_ok=True)
                    if src:
                        dest = os.path.join(dest_dir, m3u_name)
                        shutil.move(src, dest)
                        logging.info(f"M3U moved to: {dest}")
                        # convert entries to absolute paths
                        with open(dest, 'r+', encoding='utf-8') as m3u_file:
                            lines = m3u_file.readlines()
                            m3u_file.seek(0)
                            for entry in lines:
                                rel = entry.strip()
                                abs_path = os.path.abspath(os.path.join(download_path, rel))
                                m3u_file.write(abs_path + "\n")
                            m3u_file.truncate()
                        logging.info("M3U entries converted to absolute paths")
                    else:
                        logging.warning(f"M3U file not found in candidates: {candidates}")

            except Exception as e:
                logging.error(f"Process Downloads Error: {e}")
                download_info["status"] = "Error"
                self.download_history[url] = download_info

            self.socketio.emit("update_status", {"history": list(self.download_history.values())})
            self.download_queue.task_done()
