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

            # Wähle den Zielordner basierend auf dem Typ
            if download_info["type"] == "track":
                download_path = self.config.track_output
            elif download_info["type"] == "playlist":
                download_path = self.config.playlist_output
            elif download_info["type"] == "album":
                download_path = self.config.album_output
            elif download_info["type"] == "artist":
                download_path = self.config.artist_output
            else:
                download_path = self.config.track_output  # Fallback

            download_info["status"] = "Downloading..."
            self.download_history[url] = download_info
            self.socketio.emit("update_status", {"history": list(self.download_history.values())})

            try:
                logging.info(f"Downloading: {url}")

                # Nutze SpotDL's built-in M3U-Erstellung nur bei Playlists
                if download_info["type"] == "playlist":
                    playlist_name = download_info.get("name", "playlist").strip().replace("/", "-")
                    command = [
                        "spotdl",
                        "--output", download_path,
                        # Template für M3U-Datei: Playlist-Name entspricht {list-name}
                        "--m3u", "{list-name}",
                        # Nutze Web-Output-Dir falls Web-Mode
                        "--web-use-output-dir",
                        url
                    ]
                else:
                    command = [
                        "spotdl",
                        "--output", download_path,
                        url
                    ]

                logging.info(f"SpotDL command: {command}")
                self.spodtdl_subprocess = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )

                stdout, stderr = self.spodtdl_subprocess.communicate()

                if self.spodtdl_subprocess.returncode == 0:
                    download_info["status"] = "Complete"
                    logging.info("Finished Item")
                else:
                    download_info["status"] = "Failed"
                    logging.error(f"Error downloading: {stderr.strip()}")

                self.download_history[url] = download_info

                # Wenn Playlist, verschiebe die erzeugte M3U-Datei
                if download_info["type"] == "playlist":
                    # Ursprungspfad der M3U8 in download_path
                    m3u_name = f"{playlist_name}.m3u8"
                    src = os.path.join(download_path, m3u_name)
                    dest_dir = self.config.m3u_playlist_path
                    os.makedirs(dest_dir, exist_ok=True)
                    dest = os.path.join(dest_dir, m3u_name)
                    try:
                        shutil.move(src, dest)
                        logging.info(f"M3U verschoben nach: {dest}")
                        # Pfade in M3U auf absolute Pfade anpassen
                        with open(dest, 'r+', encoding='utf-8') as m3u_file:
                            lines = m3u_file.readlines()
                            m3u_file.seek(0)
                            for entry in lines:
                                rel = entry.strip()
                                # erstelle absoluten Pfad basierend auf download_path
                                abs_path = os.path.abspath(os.path.join(download_path, rel))
                                m3u_file.write(abs_path + '
')
                            m3u_file.truncate()
                        logging.info(f"M3U Pfade in absolute Pfade umgewandelt")
                    except FileNotFoundError:
                        logging.warning(f"M3U-Datei nicht gefunden: {src}")
                        logging.info(f"M3U verschoben nach: {dest}")
                    except FileNotFoundError:
                        logging.warning(f"M3U-Datei nicht gefunden: {src}")

            except Exception as e:
                logging.error(f"Process Downloads Error: {e}")
                download_info["status"] = "Error"
                self.download_history[url] = download_info

            self.socketio.emit("update_status", {"history": list(self.download_history.values())})
            self.download_queue.task_done()
