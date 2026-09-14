"""MQTT worker (QThread + paho-mqtt v2) subscribe broker → phát message.

- Tự reconnect (paho loop_forever + reconnect_delay) — không crash.
- Phát retain flag; counter/UI quyết định bỏ qua retained khi count_retained=False.
- Mất MQTT giữa chừng: tool vẫn tiếp tục đếm Receiver; khi reconnect về tiếp tục
  đếm MQTT (message cũ bị replay sẽ thành duplicate — counter bắt được).
"""

from __future__ import annotations

import random

from PyQt5.QtCore import QThread, pyqtSignal


class MqttWorker(QThread):
    message = pyqtSignal(str, str, bool)   # topic, payload, retain
    status = pyqtSignal(str)

    def __init__(self, host: str, port: int, username: str = "",
                 password: str = "", topics: list[str] | None = None,
                 client_id: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.topics = list(topics or [])
        self.client_id = client_id or "gmc-%06x" % random.randint(0, 0xFFFFFF)
        self._client = None
        self._stop = False

    def stop(self) -> None:
        self._stop = True
        try:
            if self._client:
                self._client.disconnect()
        except Exception:
            pass
        self.wait(4000)

    def set_topics(self, topics: list[str]) -> None:
        self.topics = list(topics)

    def run(self) -> None:
        import paho.mqtt.client as mqtt

        self._stop = False
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
                client_id=self.client_id,
                clean_session=True,
            )
        except Exception as e:
            self.status.emit("MQTT ERROR: %s" % e)
            return

        self._client = client
        client.reconnect_delay_set(min_delay=1, max_delay=30)

        def on_connect(c, u, flags, rc):
            if rc != 0:
                self.status.emit("MQTT CONNECT FAIL rc=%d" % rc)
                return
            for t in self.topics:
                c.subscribe(t, qos=0)
            self.status.emit("CONNECTED %s:%d" % (self.host, self.port))

        def on_disconnect(c, u, rc):
            if not self._stop:
                self.status.emit("DISCONNECTED (reconnecting...)")

        def on_subscribe(c, u, mid, granted_qos):
            self.status.emit("SUBSCRIBED %d topic(s)" % len(self.topics))

        def on_message(c, u, msg):
            try:
                payload = msg.payload.decode("utf-8", errors="replace")
            except Exception:
                payload = ""
            self.message.emit(msg.topic, payload, msg.retain)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_subscribe = on_subscribe
        client.on_message = on_message

        if self.username or self.password:
            client.username_pw_set(self.username, self.password)

        self.status.emit("CONNECTING %s:%d" % (self.host, self.port))
        try:
            client.connect(self.host, self.port, keepalive=60)
            client.loop_forever(retry_first_connection=True)
        except Exception as e:
            if not self._stop:
                self.status.emit("MQTT ERROR: %s" % e)
        finally:
            try:
                client.disconnect()
            except Exception:
                pass
            self._client = None
