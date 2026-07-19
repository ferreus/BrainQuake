#! /usr/bin/python3.7
# -- coding: utf-8 -- **

import socket
import time
import pickle
import os
import logging
import tqdm

logger = logging.getLogger(__name__)

HEADERSIZE = 10
SEPARATOR = '<SEPARATOR>'
BUFFER_SIZE = 4096
# host = '166.111.152.123'
# port = 6669
Filepath = '.'

def create_socket(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # s.connect((socket.gethostname(), 1241))
    s.connect((host, port))
    return s

def _recv_exact(socket, n):
## keep receiving until exactly n bytes have been read
    buf = b''
    while len(buf) < n:
        chunk = socket.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('socket closed before expected data was received')
        buf += chunk
    return buf

def text_send(socket, msg):
    payload = pickle.dumps(msg)
    body = bytes(f'{len(payload):<{HEADERSIZE}}', 'utf-8') + payload
    # pad with raw bytes (not pickled) so the total length lands exactly
    # on a BUFFER_SIZE boundary that text_recv can match on the other end
    padded_len = -(-len(body) // BUFFER_SIZE) * BUFFER_SIZE
    body += b'0' * (padded_len - len(body))
    socket.sendall(body)

def text_recv(socket):
## receive a text and print it out in the terminal
    header = _recv_exact(socket, HEADERSIZE)
    msglen = int(header)
    padded_len = -(-(HEADERSIZE + msglen) // BUFFER_SIZE) * BUFFER_SIZE
    rest = _recv_exact(socket, padded_len - HEADERSIZE)
    txt_recv = pickle.loads(rest[:msglen])
    return txt_recv

def file_send(socket, pat_name):
## send a file and its file info to the server
    pat_filepath = os.path.join('data', 'send', pat_name+'.nii.gz')
    filename = pat_name+'.nii.gz'
    filesize = os.path.getsize(pat_filepath)
    socket.send(f'{filename}{SEPARATOR}{filesize}'.encode())

    progress = tqdm.tqdm(range(filesize), f'Sending {filename}', unit="B", unit_scale=True, unit_divisor=1024)
    with open(pat_filepath, "rb") as f:
        for _ in progress:
            # read the bytes from the file
            bytes_read = f.read(BUFFER_SIZE)
            if not bytes_read:
                # file transmitting is done
                logger.debug("transmission completed")
                break
            # we use sendall to assure transimission in
            # busy networks
            socket.sendall(bytes_read)
            # update the progress bar
            progress.update(len(bytes_read))
            # int(len(bytes_read)/filesize)
    socket.close()

def file_recv(socket):
## receive a gifti file from the server
    # receive the file info
    received = socket.recv(BUFFER_SIZE).decode()
    filename, filesize = received.split(SEPARATOR)
    # remove absolute path if there is
    filename = os.path.basename(filename)
    filepath = os.path.join(Filepath, 'data', 'down', filename)
    # convert to integer
    filesize = int(filesize)

    # start receiving the file from the socket and writing to the file stream
    progress = tqdm.tqdm(range(filesize), f'Receiving {filename}', unit="B", unit_scale=True, unit_divisor=1024)
    with open(filepath, "wb") as f:
        for _ in progress:
            # read bytes from the socket (receive)
            bytes_read = socket.recv(BUFFER_SIZE)
            if not bytes_read:
                # nothing is received
                # file transmitting is done
                logger.debug("transmission completed")
                break
            # write to the file the bytes we just received
            f.write(bytes_read)
            # update the progress bar
            progress.update(len(bytes_read))
    socket.close()
