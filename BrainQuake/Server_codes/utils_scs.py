#! /usr/bin/python3.7
# -- coding: utf-8 -- **

import sys
import socket
import time
import pickle
import os
import logging
import tqdm
import utils

logger = logging.getLogger(__name__)

HEADERSIZE = 10
SEPARATOR = '<SEPARATOR>'
BUFFER_SIZE = 4096
host = '0.0.0.0'
port = 6669
FILEPATH = os.path.join(os.getcwd(), 'data', 'recv') # '/home/hello/reconModule_test/testCS/data/recv'

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
    logger.debug(f"text_send: sending {len(payload)}B payload: {msg!r}")
    socket.sendall(body)

def text_recv(socket):
## receive a text and print it out in the terminal
    header = _recv_exact(socket, HEADERSIZE)
    msglen = int(header)
    padded_len = -(-(HEADERSIZE + msglen) // BUFFER_SIZE) * BUFFER_SIZE
    rest = _recv_exact(socket, padded_len - HEADERSIZE)
    txt_recv = pickle.loads(rest[:msglen])
    logger.debug(f"text_recv: received {msglen}B payload: {txt_recv!r}")
    return txt_recv

def file_recv(socket, reconType='recon-all'):
## receive a nifti file from the client
    # receive the file info
    received = socket.recv(BUFFER_SIZE).decode()
    filename, filesize = received.split(SEPARATOR)
    filename = os.path.basename(filename)
    # filepath = os.path.join('data', 'recv', filename)
    filesize = int(filesize)
    pat_name = filename.split('.')[0].split('T')[0]
    logger.info(f"Upload starting: file={filename} size={filesize}B patient={pat_name} reconType={reconType}")
    if os.path.exists(f"{FILEPATH}/{pat_name}"):
        filepath = os.path.join('data', 'recv', pat_name, filename)
    else:
        logger.info(f"Creating patient directory {FILEPATH}/{pat_name}")
        os.system(f"mkdir {FILEPATH}/{pat_name}")
        filepath = os.path.join('data', 'recv', pat_name, filename)

    # start receiving the file from the socket and writing to the file stream
    t0 = time.time()
    bytes_written = 0
    with open(filepath, "wb") as f:
        while True:
            # read bytes from the socket (receive)
            bytes_read = socket.recv(BUFFER_SIZE)
            if not bytes_read:
                break
            f.write(bytes_read)
            bytes_written += len(bytes_read)
    f.close()
    logger.info(f"Upload complete: {filepath} ({bytes_written}B received in {time.time() - t0:.1f}s)")
    number = utils.add_a_log(name=pat_name, hospital='Yuquan', reconType=reconType)
    logger.info(f"Task {number} queued (state=wait) for patient={pat_name} reconType={reconType}")
    socket.close()
    return number

def file_recvCT(socket):
    received = socket.recv(BUFFER_SIZE).decode()
    filename, filesize = received.split(SEPARATOR)
    filename = os.path.basename(filename)
    # filepath = os.path.join('data', 'recv', filename)
    filesize = int(filesize)
    pat_name = filename.split('.')[0].split('C')[0]
    logger.info(f"CT upload starting: file={filename} size={filesize}B patient={pat_name}")
    if os.path.exists(f"{FILEPATH}/{pat_name}"):
        os.system(f"mkdir {FILEPATH}/{pat_name}/fslresults")
        filepath = os.path.join('data', 'recv', pat_name, filename)
    else:
        logger.info(f"Creating patient directory {FILEPATH}/{pat_name}")
        os.system(f"mkdir {FILEPATH}/{pat_name}")
        os.system(f"mkdir {FILEPATH}/{pat_name}/fslresults")
        filepath = os.path.join('data', 'recv', pat_name, filename)

    t0 = time.time()
    bytes_written = 0
    with open(filepath, "wb") as f:
        while True:
            bytes_read = socket.recv(BUFFER_SIZE)
            if not bytes_read:
                break
            f.write(bytes_read)
            bytes_written += len(bytes_read)
    f.close()
    logger.info(f"CT upload complete: {filepath} ({bytes_written}B received in {time.time() - t0:.1f}s)")
    socket.close()
    return pat_name

def file_send(filepath, socket):
    filename = filepath.split('/')[-1]
    filesize = os.path.getsize(filepath)
    logger.info(f"Sending file {filepath} ({filesize}B) to client")
    socket.send(f'{filename}{SEPARATOR}{filesize}'.encode())

    # progress = tqdm.tqdm(range(filesize), f'Sending {filename}', unit="B", unit_scale=True, unit_divisor=1024)
    time.sleep(1)
    t0 = time.time()
    with open(filepath, "rb") as f:
        # for _ in progress:
        while True:
            # read the bytes from the file
            bytes_read = f.read(BUFFER_SIZE)
            if not bytes_read:
                break
            # we use sendall to assure transimission in
            # busy networks
            socket.sendall(bytes_read)
            # update the progress bar
            # progress.update(len(bytes_read))
    logger.info(f"Finished sending {filepath} in {time.time() - t0:.1f}s")
    socket.close()
