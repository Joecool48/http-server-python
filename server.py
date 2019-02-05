import socket
import sys
import threading
import os
import time
import signal

from email.utils import formatdate

from datetime import datetime
from time import mktime

def get_http_formatted_date():
    return formatdate(timeval=None, localtime=False, usegmt=True)

class Server:
    server_host = "192.168.1.13"
    server_port = 2402
    serversocket = None
    server_state = "stopped"
    control_thread = None
    run_thread = None
    clients = {}
    MAX_HEADER_SIZE = 1000 # if more than 1000 return error
    SERVER_DIRECTORY = "/home/joey/pulse-high-school-2019" # no trailing slash
    MAX_NUM_REQUESTS_PER_MINUTE = 10
    host_num_requests = {} # keeps track of the number of requests per host so they can be rate limited
    TIMEOUT_SECONDS = 1
    lock = threading.Lock()
    def __init__(self):
        signal.signal(signal.SIGINT, self.interrupt_handler) # set the signal interrupt handler

    def start(self):
        self.control_thread = threading.Thread(target=self.control, name="control")
        self.control_thread.start()
 
    
    def create(self):
        try:
            self.serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except socket.error as msg:
            self.serversocket = None
        try:
            self.serversocket.bind((self.server_host, self.server_port)) # bind to the found ip
            self.serversocket.listen(50)
        except socket.error as msg:
            self.serversocket.close()
            self.serversocket = None
        if self.serversocket is None:
            print("Could not open a server socket :/")
            sys.exit(1)
    # listens for incoming user command line commands
    def control(self):
        while True:
            user_input = input("")
            user_input = user_input.split(" ") # split on spaces
            print("User input is:", user_input[0])
            if len(user_input) == 0:
                continue
            elif user_input[0] == "exit":
                self.shutdown()
            elif user_input[0] == "stop":
                if self.server_state != "running":
                    print("Can't stop a server that isn't running")
                else:
                    print("Stopping server")
                    self.stop()
            elif user_input[0] == "start":
                if self.server_state == "running":
                    print("Can't start a server that is already running")
                else:
                    print("Starting server")
                    self.run_thread = threading.Thread(target = self.run, name="run")
                    self.run_thread.start()
            elif user_input[0] == "sethostname" or user_input[0] == "setportnumber":
                if self.server_state != "stopped":
                    print("Please stop the server before configuring")
                elif len(user_input) < 2:
                    print("Please supply a name")
                elif user_input[0] == "sethostname":
                    self.sethostname(user_input[1])
                elif user_input[0] == "setportnumber":
                    self.setportnumber(user_input[1])
                else:
                    print("Couldn't find command")
    def stop(self):
        self.server_state = "stopped"
        self.serversocket.close()
    def interrupt_handler(self, sig, frame):
        self.shutdown()
    def shutdown(self):
        self.stop()
        sys.exit(0) # exit gracefully
    def sethostname(self, hostname):
        self.server_host = hostname
        print("Server host name set to {}".format(hostname))
    def setportnumber(self, portnum):
        self.server_port = int(portnum)
        print("Set server port to {}".format(str(portnum)))
    def send_error(self, sock, err_num, message):
        response = "{} {} HTTP/1.1".format(err_num, message)
        sock.sendall(response.encode())
    # launches a control thread and a listen thread from main
    def run(self):
        print("Server starting up at address {} and port {}".format(self.server_host, self.server_port))
        self.create()
        self.server_state = "running"
        print("Running...")

        curr_time = time.gmtime(0) # get time for rate limiting hosts
        while True:
            while self.server_state != "stopped":
                print("Looking for clients...")
                new_sock, new_addr = self.serversocket.accept()
                print("Connected with {}".format(new_addr[0]))
                new_time = time.gmtime(0)
                if new_time.tm_sec - curr_time.tm_sec >= 60: # erase the map since 60 seconds elapsed
                    self.host_num_requests.clear()
                    curr_time = new_time
                if new_addr[0] not in self.host_num_requests:
                    self.host_num_requests[new_addr] = 1
                else:
                    self.host_num_requests[new_addr] += 1
                if self.host_num_requests[new_addr] > self.MAX_NUM_REQUESTS_PER_MINUTE:
               # send them a nice reminder
                    self.send_error(new_sock, 429, "Too many requests")
                    new_sock.close()
                    continue # ignore their request

                thread = threading.Thread(target=self.service_client, args=(new_sock,))
                thread.start()
                self.clients[new_sock.fileno()] = thread # store the value of the new socket and thread

    def exit_thread(self, sock):
         # must've lost connection. Destroy socket and end thread
        self.lock.acquire()
        # go through clients and delete the right tup
        del self.clients[sock.fileno()]
        self.lock.release()
        sock.close()
    def get_request_dict(self, sock, header):
        full_str = header
        request_line, headers_alone = header.split('\r\n', 1)
        request = {}
        for i in headers_alone.split('\r\n'):
            s = i.split(": ") # split on : delimited strings

            if len(s) < 2:
                continue
            request[s[0]] = s[1]
        # if post, then get the rest of the data
        req_split = request_line.split(" ")
        if len(req_split) != 3:
            self.send_error(sock, 400, "Bad request")
            self.exit_thread(sock)
            return 400
        elif req_split[0] != "GET" and req_split[0] != "POST" and req_split[0] != "DELETE":
            # unsupported
            self.send_error(sock, 405, "Method not supported")
            self.exit_thread(sock)
            return 405
        request["path"] = req_split[1] # throw the path into the mix
        request["command"] = req_split[0]
        if len(header) >= 4 and header[0:4] == "POST":
            if "Content-Length" not in request.keys():
                self.send_error(sock, 411, "Length field required for POST")
                self.exit_thread(sock)
                return 411
            try:
                body = sock.recv(int(request["Content-Length"]))
                full_str += body.decode("utf-8")
                request["body"] = body.decode("utf-8")
            except socket.error as msg:
                self.send_error(sock, 400, "Bad request")
                self.exit_thread(sock)
                return 400
            if int(request["Content-Length"]) >= self.MAX_HEADER_SIZE:
                self.send_error(sock, 413, "Payload too large")
                self.exit_thread(sock)
                return 413
        print(request)
        return request

    def get_rest_of_string(self, new_data, sock):
        curr_str = new_data.decode("utf-8")
        curr_char = None
        sock.settimeout(self.TIMEOUT_SECONDS)
        # check for windows and or linux line endings
        while True:
            try:
                curr_char = sock.recv(1)
            except Exception as e:
                self.send_error(sock, 408, "Request timeout")
                self.exit_thread(sock)
            curr_str += curr_char.decode("utf-8")
            if len(curr_str) >= 4 and curr_str[-4:] == "\r\n\r\n":
                break
            elif len(curr_str) >= self.MAX_HEADER_SIZE:
               self.send_error(sock, 413, "Payload too large")
               return None
        return self.get_request_dict(sock, curr_str)

    def get_request(self, sock, request):
        # check if the resource exists
        f = None
        try:
            f = open(self.SERVER_DIRECTORY + request["path"])
        except Exception as e:
            self.send_error(sock, 404, "Resource not found")
            self.exit_thread(sock)
            return 404
        response = "200 OK HTTP/1.1\r\n"
        file_contents = f.read()
        # append the length and the contents
        response += "Date: "
        response += get_http_formatted_date()
        response += "\r\n"
        response += "Content-Length: "
        response += str(len(file_contents))
        response += "\r\n"
        response += "\r\n"
        response += file_contents
        # finished constructing object. Now to send it
        sock.sendall(response.encode())
        return 200 # indicate that the request was good

    def post_request(self, sock, request):
        if request["path"][0] != '/':
            self.send_error(sock, 403, "Could not access resource")
            self.exit_thread(sock)
            return 403
        else:
            f = None
            try:
                f = open(self.SERVER_DIRECTORY + request["path"], "w+") # create file
                f.write(request["body"])
            except Exception as e:
                self.send_error(sock, 400, "Could not create resource")
                self.exit_thread(sock)
                return 400

            # send a response saying that it succeeded
            response = "201 Created HTTP/1.1\r\n"
            response += "Date: "
            response += get_http_formatted_date()
            response += "\r\n"
            response += "\r\n" # nothing else to put because post request
            sock.sendall(response.encode())
            return 201

    def delete_request(self, sock, request):
        if request["path"][0] != '/':
            self.send_error(sock, 403, "Could not access resource")
            self.exit_thread(sock)
            return 403
        else:
            try:
                os.remove(self.SERVER_DIRECTORY + request["path"])
            except Exception as e:
                self.send_error(sock, 404, "Could not find resource")
                self.exit_thread(sock)
                return 404
            response = "200 OK HTTP/1.1\r\n"
            response += "Date: "
            response += get_http_formatted_date()
            response += "\r\n"
            response += "\r\n"
            sock.sendall(response.encode())
            return 200
            
    def service_client(self, sock):
        # check for available data. If returns error, connection mustve closed
        try:
            new_data = sock.recv(1)
        except socket.error as msg:
            self.exit_thread(sock)
        # closed connection
        if new_data == "":
            self.exit_thread(sock)
        # must have some valid data. So take data till either CRLF or LF ending
        request = self.get_rest_of_string(new_data, sock) # pass in begin of string and sock
        if type(request) != dict:
            return # something went wrong and it sent error
        print("Got request: ", request)
        # next decode the client's request
        # find out if GET or POST request
        if request["command"] == "GET":
            self.get_request(sock, request)
        elif request["command"] == "POST":
            self.post_request(sock, request)
        elif request["command"] == "DELETE":
            self.delete_request(sock, request)
        else:
            self.send_error(sock, 405, "Method not allowed")
            self.exit_thread(sock)
            return
        self.exit_thread(sock) # No keep alive here
        print("Finished sending data")
        return

if __name__ == "__main__":
    server = Server()
    server.start()
