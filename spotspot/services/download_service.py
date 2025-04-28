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
        download_info = {
            "url": spotify_url,
            "type": data.get("type"),
            "name": data.get("name"),
            "artist": data.get("artist"),
            "status": "Pending..."
        }
        self.download_queue.put((spotify_url, download_info))
        self.download_history[spotify_url] = download_info
        self._emit_update()

    def _ensure_writable(self, path):
        try:
            os.chmod(path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
            logging.debug(f"Set writable permissions on {path}")
        except Exception as e:
            logging.warning(f"Could not chmod {path}: {e}")

    def _emit_update(self):
        self.socketio.emit("update_status", {"history": list(self.download_history.values())})

    def _prepare_download_path(self, download_info):
        try:
            path = self.config.track_output.format_map(download_info)
        except Exception:
            path = self.config.track_output
        os.makedirs(path, exist_ok=True)
        self._ensure_writable(path)
        parent = os.path.dirname(path) or path
        self._ensure_writable(parent)
        return path

    def _build_spotdl_command(self, url, download_info):
        if download_info.get("type") == "playlist":
            playlist_name = (
                download_info.get("name", "playlist").strip().replace("/", "-") + ".m3u"
            )
            return [
                "spotdl", "--output", ".",
                "--m3u", playlist_name,
                "--log-level", self.config.log_level,
                "--print-errors", url
            ]
        else:
            return ["spotdl", "--output", ".", url]

    def _execute_download(self, command, cwd):
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        for line in proc.stdout:
            logging.info(line.rstrip())
        return proc.wait()

    def process_downloads(self):
        try:
            self.config.get_spotdl_vars()
            self.config.create_config_file()
        except Exception:
            logging.warning("Proceeding without writing SpotDL config file.")

        while True:
            url, download_info = self.download_queue.get()
            if download_info.get("status") == "Cancelled":
                self.download_queue.task_done()
                continue

            download_path = self._prepare_download_path(download_info)

            download_info["status"] = "Downloading..."
            self.download_history[url] = download_info
            self._emit_update()

            try:
                logging.info(f"Downloading: {url}")
                command = self._build_spotdl_command(url, download_info)
                logging.info(f"SpotDL command: {command} (cwd={download_path})")
                returncode = self._execute_download(command, cwd=download_path)
                if returncode != 0:
                    logging.error(f"SpotDL exit code {returncode}")
                    download_info["status"] = "Failed"
                else:
                    download_info["status"] = "Complete"
            except Exception as e:
                logging.error(f"Process Downloads Error: {e}")
                download_info["status"] = "Error"

            self.download_history[url] = download_info
            self._emit_update()
            self.download_queue.task_done()
