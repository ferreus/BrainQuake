#! /usr/bin/python3.7
# -- coding: utf-8 -- **

import sys
import socket
import time
import pickle
import os
import logging
import tqdm
import multiprocessing
import utils
import utils_scs

logger = logging.getLogger(__name__)

HEADERSIZE = 10
SEPARATOR = '<SEPARATOR>'
BUFFER_SIZE = 4096
host = '0.0.0.0'
port = 6669
FILEPATH = os.getenv('SUBJECTS_DIR') # '/usr/local/freesurfer/subjects'
FILEPATH1 = os.path.join(os.getcwd(), 'data', 'recv') # '/home/hello/reconModule_test/testCS/data/recv'

def recv_a_t1(clientsocket, task):
    task_flag = 1 # a task starts here
    fs_flag = 0 # a freesurfer recon task has not been completed
    # receive a T1 file
    if task == '10':
        reconType = f"recon-all"
        number = utils_scs.file_recv(clientsocket, reconType)
    elif task == '11':
        reconType = f"fast-surfer"
        number = utils_scs.file_recv(clientsocket, reconType)
    elif task == '12':
        reconType = f"infant-surfer"
        number = utils_scs.file_recv(clientsocket, reconType)
    logger.info(f"T1 file received for task={task} reconType={reconType} -> assigned {number}")
    # here we read the log
    log, i = utils.read_a_log(num=number)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # s.bind((socket.gethostname(), 1241))
    s.bind((host, 6666))
    s.listen(5)
    clientsocket, address = s.accept()
    logger.info(f"Sending confirmation log for {number} back to client {address}")
    time.sleep(1)
    utils_scs.text_send(clientsocket, log)
    clientsocket.close()
    s.close()
    fs_flag = 1 # a freesurfer recon task has been completed
    logger.info(f"Upload handling for {number} complete; task is now queued (state=wait) for the poller in combine.py")
    return

def recv_a_ct(clientsocket):
    name = utils_scs.file_recvCT(clientsocket)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # s.bind((socket.gethostname(), 1241))
    s.bind((host, 6667))
    s.listen(5)
    clientsocket, address = s.accept()
    logger.info(f"Connection from {address} has been established")
    time.sleep(1)
    utils_scs.text_send(clientsocket, 'Uploaded!')
    clientsocket.close()
    s.close()
    logger.info(f"CT received for patient={name}; spawning registerrun process")
    p = multiprocessing.Process(target=utils.registerrun,args=(name,))
    p.start()
    p.join()
    logger.info(f"registerrun process for patient={name} has exited")
    return

def send_fsls(clientsocket):
    clientsocket.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # s.bind((socket.gethostname(), 1241))
    s.bind((host, 6668))
    s.listen(5)
    clientsocket, address = s.accept()
    logger.info(f"Connection from {address} has been established")
    time.sleep(1)
    patName = utils_scs.text_recv(clientsocket)
    filepath = f"{FILEPATH1}/{patName}/fslresults/{patName}intracranial.nii.gz"
    logger.info(f"Sending {filepath} to {address}")
    utils_scs.file_send(filepath, clientsocket)
    logger.info(f"Sent {filepath} to {address}")
    clientsocket.close()
    s.close()
    return

def check_recon(clientsocket):
    clientsocket.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # s.bind((socket.gethostname(), 1241))
    s.bind((host, 6665))
    s.listen(5)
    clientsocket, address = s.accept()
    logger.info(f"Connection from {address} has been established")
    time.sleep(1)
    check_log = utils_scs.text_recv(clientsocket)
    logger.debug(f"check_recon: received check_log={check_log}")
    [num, name, hospital, state, info] = check_log.split(' ')
    if num == 'None':
        num = None
    if name == 'None':
        name = None
    logger.info(f"Status check requested: num={num} name={name} hospital={hospital}")
    logs, i = utils.task_log(req='client', num=num, name=name, hospital=hospital)
    logger.debug(f"check_recon: sending logs={logs}")
    time.sleep(1)
    utils_scs.text_send(clientsocket, logs)
    time.sleep(2)
    clientsocket.close()
    s.close()
    return

def send_recon(clientsocket):
    clientsocket.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # s.bind((socket.gethostname(), 1241))
    s.bind((host, 6664))
    s.listen(5)
    clientsocket, address = s.accept()
    logger.info(f"Connection from {address} has been established")
    time.sleep(1)
    down_log = utils_scs.text_recv(clientsocket)
    logger.debug(f"send_recon: received down_log={down_log}")
    for log in down_log:
        [num, name, hospital, reconType, state, info] = log.split(' ')
        filepath = f"{FILEPATH}/{name}.zip"
        logger.info(f"Sending {filepath} to {address}")
        utils_scs.file_send(filepath, clientsocket)
        logger.info(f"Sent {filepath} to {address}")
    clientsocket.close()
    s.close()
    return
