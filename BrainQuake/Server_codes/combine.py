#! /usr/bin/python3.7
# -- coding: utf-8 -- **

import os
import time
import logging
import multiprocessing
import utils as utils

logger = logging.getLogger(__name__)

CHECKTIME = 2
Filepath = os.path.join(os.getcwd(), 'data', 'recv', 'task_done.txt') # './data/recv/task_done.txt'

while True: ## poll and recon
    # wait for a poll
    time.sleep(CHECKTIME)

    # check for the last finished task
    f = open(Filepath)
    lines = f.readlines()
    num = "#1"
    if len(lines) > 0:
        last_line = lines[-1]
        num = last_line.split(" ")[0]
    f.close()
    print(f"Last finished task is: {num}.")
    logger.info(f"Poll tick: last finished task is {num}")

    # send a check request to task_log.py
    req = "polling"
    new_flag, log = utils.task_log(req, num)

    # start a recon task or wait for the next poll
    if new_flag:
        num, name, hospital, reconType, state, info = utils.divide_a_log(log)
        logger.info(f"Poller picked up task {num} (patient={name}, reconType={reconType}); launching runner process")
        #cmd, num, name, hospital, state, info = utils.write_a_freecmd(log)
        if reconType == 'recon-all':
            cmd = utils.write_a_freecmd(log)
            p1 = multiprocessing.Process(target=utils.reconrun,args=(cmd,num,name,hospital,reconType,))
        elif reconType == 'fast-surfer':
            cmd = utils.write_a_fastcmd(log)
            p1 = multiprocessing.Process(target=utils.fastrun,args=(cmd,num,name,hospital,reconType,))
        elif reconType == 'infant-surfer':
            cmd = utils.write_a_infantcmd(log)
            p1 = multiprocessing.Process(target=utils.infantrun,args=(cmd,num,name,hospital,reconType,))
        # p1 = multiprocessing.Process(target=utils.run,args=(cmd,num,name,hospital,))
        # p2 = multiprocessing.Process(target=utils.estimate,args=(num,name,hospital,state,info,))
        p1.start()
        # p2.start()

    else:
        logger.debug("Poll tick: no new waiting task found")

    # wait for the next poll
    time.sleep(3*CHECKTIME)
    
