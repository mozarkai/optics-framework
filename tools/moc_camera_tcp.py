import asyncio
import io
import struct
from asyncio import Server, StreamReader, StreamWriter
from typing import Callable, Coroutine
from PIL import Image
import logging

# Set up basic logging to replace Logz
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CaptureScreenshotCallable = Callable[[], Coroutine[None, None, bytes]]


class ScreenshotTcpServer:
    def __init__(
        self,
        port: int,
        capture_screenshot_fn: CaptureScreenshotCallable,
        host: str = "127.0.0.1",
    ):
        self._host = host
        self._port = port
        self._server: Server | None = None
        self._should_stop_event = asyncio.Event()
        self._capture_screenshot_fn = capture_screenshot_fn
        self.resource_id = f"ScreenshotServer:{host}:{port}"

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        logger.info(f"Asyncio TCP server listening on {self._host}:{self._port}")
        asyncio.create_task(self._server.serve_forever())

    async def stop(self):
        logger.info(f"Stopping server {self.resource_id} ...")
        self._should_stop_event.set()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info(f"Server stopped {self.resource_id}")
        else:
            logger.info(f"Server {self.resource_id} is already None.")

    async def _handle_client(self, reader: StreamReader, writer: StreamWriter):
        addr = writer.get_extra_info("peername")
        logger.info(f"Client connected from {addr}")

        try:
            while not self._should_stop_event.is_set():
                try:
                    data = await asyncio.wait_for(reader.readline(), timeout=5.0)
                except asyncio.TimeoutError:
                    await asyncio.sleep(0.1)
                    continue

                if not data:
                    logger.info(f"Client {addr} disconnected")
                    break

                command = data.decode("utf-8").strip()
                logger.info(f"Received command from {addr}: '{command}'")

                if command == "screenshot":
                    try:
                        img_bytes = await self._capture_screenshot_fn()
                        img_size = len(img_bytes)
                        packed_img_size = struct.pack(">I", img_size)
                        logger.debug(
                            f"Sending screenshot to the client {addr}, image size: {img_size}"
                        )
                        writer.write(packed_img_size)
                        writer.write(img_bytes)
                        await writer.drain()
                        logger.info(f"screenshot sent to {addr}")
                    except (ConnectionResetError, BrokenPipeError) as e:
                        logger.error(
                            f"Client {addr} disconnected during send, error: {e}"
                        )
                        break
                    except Exception as e:
                        logger.error(
                            f"Client {addr} disconnected during send, error: {e}"
                        )
                else:
                    logger.warning(f"Unrecognized command from {addr}: '{command}'")

        except Exception as e:
            logger.error(f"Error handling client {addr}: {e}")
        finally:
            writer.close()
            await writer.wait_closed()
            logger.info(f"Connection with {addr} closed")


async def _create_dummy_img_bytes() -> bytes:
    # Create a 100x50 red RGB image
    width = 100
    height = 50
    color = (255, 0, 0)  # Red
    img = Image.new("RGB", (width, height), color=color)
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format="JPEG")
    return img_byte_arr.getvalue()


async def main():
    ss = ScreenshotTcpServer(port=8000, capture_screenshot_fn=_create_dummy_img_bytes)
    await ss.start()
    try:
        # Run server for 30 seconds
        await asyncio.sleep(1000)
    finally:
        await ss.stop()


if __name__ == "__main__":
    asyncio.run(main())
