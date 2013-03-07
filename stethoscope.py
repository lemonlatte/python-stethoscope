import os
import json
from collections import deque

import gevent
from gevent import pywsgi
from gevent_zeromq import zmq
from gevent_subprocess import Popen, PIPE
from geventwebsocket.handler import WebSocketHandler


BUFFER_SIZE = 10
LOG_INCOME_PORT = 5558
LOG_OUTCOME_PORT = 5559


class MessageBuffer(deque):

    def __init__(self, size):
        deque.__init__(self)
        self.size = size

    def full_append(self, item):
        deque.append(self, item)
        self.popleft()

    def append(self, item):
        deque.append(self, item)
        if len(self) == self.size:
            self.append = self.full_append


msg_buffer = {}
spawn_dict = {}


def msg_in_json(state, msg):
    ret = {
        "state": state,
        "msg": msg
    }
    return json.dumps(ret)


def publish_log(context, ip, path):
    context = zmq.Context()
    msg_publisher = context.socket(zmq.PUB)
    msg_publisher.connect("tcp://%s:%d" % (ip, LOG_INCOME_PORT))

    tail = Popen(["tail", "-f", "-n100", path], stdout=PIPE)
    while True:
        data = tail.stdout.readline()
        msg_publisher.send("%s %s" % (path, data))
        gevent.sleep(0.5)


def message_relay(context):
    msg_subscriber = context.socket(zmq.SUB)
    msg_subscriber.bind("tcp://*:%d" % LOG_INCOME_PORT)
    msg_subscriber.setsockopt(zmq.SUBSCRIBE, "")
    msg_publisher = context.socket(zmq.PUB)
    msg_publisher.bind("tcp://127.0.0.1:%d" % LOG_OUTCOME_PORT)
    while True:
        string = msg_subscriber.recv()
        # TODO: Use regex to match the messages, rather than split
        path, data = string.split(" ", 1)
        msg_buffer[path].append(data)
        msg_publisher.send(string)


class WebLogServer(object):

    def __init__(self, context):
        self.context = context

    def __call__(self, environ, start_response):
        ws = environ['wsgi.websocket']
        # Prepare connection to message relay
        subscriber = self.context.socket(zmq.SUB)
        subscriber.connect("tcp://127.0.0.1:%d" % LOG_OUTCOME_PORT)
        print "Connecting from: %s" % environ["REMOTE_ADDR"]
        path = None
        while True:
            ws_data = ws.receive()
            if ws_data is None:
                # Close the connection if we receive the close signal from browser.
                print  "Close connection from %s" % environ["REMOTE_ADDR"]
                return

            if not path:
                # Prepare log queue and log worker if the path is not existed.
                if os.path.exists(ws_data):
                    path = str(ws_data)
                    # Setting the socket options and filtering it by path.
                    subscriber.setsockopt(zmq.SUBSCRIBE, path)
                    if not path in msg_buffer:
                        msg_buffer[path] = MessageBuffer(BUFFER_SIZE)
                    else:
                        for msg in msg_buffer[path]:
                            ws.send(msg_in_json("data", msg))
                    if not path in spawn_dict:
                        spawn_dict[path] = gevent.spawn(publish_log, self.context, "127.0.0.1", path)
                else:
                    ws.send(msg_in_json("fail", "File is not existed."))
                    continue

            data = subscriber.recv()
            ws.send(msg_in_json("send", data))


html_template = open("client.html", "r").read()


def home(environ, start_response):
    start_response('200 OK', [('Content-type', 'text/html')])
    return html_template


def run():
    context = zmq.Context()
    print "\nInitialize Stethoscope Server..."
    print " - Launching Stethoscope Message Relay Server"
    gevent.spawn(message_relay, context)
    print " - Launching Stethoscope Web Server"
    web_server = pywsgi.WSGIServer(("127.0.0.1", 8088), home)
    web_server.start()
    print " - Launching Stethoscope Websocket Server"
    ws_server = pywsgi.WSGIServer(("127.0.0.1", 9998), WebLogServer(context), handler_class=WebSocketHandler)
    print "Stethoscope Server is ready"
    ws_server.serve_forever()

if __name__ == "__main__":
    run()
