#!/usr/bin/python

"""
This is a babysitter for Mongrel2.  It spins it up and pings it regularly
to make sure it's still up, if it isn't it attempts to restart. It's designed
right now for rapid prototyping, it takes all the HOSTS defined in config.py and 
prepends them to your /etc/hosts file.  If you run m2.py and each service with 
run.py in its own terminal you have a simple but powerful rapid development 
environment ready to go.

My goal is to provide a full API for configuring all aspects of Mongrel2. Between
its elegant sqlite config schema and its control port everything is there to
provide a very powerful production API for Mongrel2.

"""

import os, sys, re, subprocess, signal, time, errno

try:
    import zmq
    from zmq.eventloop.ioloop import PeriodicCallback, DelayedCallback
    from zmq.eventloop.zmqstream import ZMQStream
except ImportError as e:
    print('You must have pyzmq installed.')
    sys.exit(1)

try:
    from mongrel2 import tnetstrings
except ImportError as e:
    print('You must have mongrel2 installed.')
    sys.exit(1)


# constants

try:
    from config import m2, M2_PID_PATH, PATHS, HOSTS
except ImportError as e:
    print('You must create a config.py.')

CHECKUP_INTERVAL = 2000
CHECKUP_TIMEOUT = CHECKUP_INTERVAL / 4
PAUSE_BEFORE_RESTART = CHECKUP_INTERVAL / 6
PAUSE_AFTER_RESTART = PAUSE_BEFORE_RESTART
STOP_TIMEOUT = CHECKUP_TIMEOUT

M2_CONTROL_PORT = "ipc://{0}/run/m2.port".format(os.getcwd())

HOST_PATTERN = '^\s*127.0.0.1\s+{0}\s*$'


# helpers

def print_output(frames):
    print(frames[0])

class M2LoadException(Exception):
    pass

def add_hosts(hosts):
    """
    Add `hosts` to /etc/hosts. We prepend the rules so OS X Lion
    picks them up.
    """
    lines = []
    to_prepend = []
    with open('/etc/hosts', 'r') as f:
        lines = f.readlines()
        for host in hosts.values():
            pattern = re.compile(HOST_PATTERN.format(host))
            found = False
            for line in lines:
                if re.match(pattern, line):
                    found = True
                    continue
            if not found:
                to_prepend.append('127.0.0.1 {0}'.format(host))
    with open('/etc/hosts', 'w') as f:
        f.write('\n'.join(to_prepend) + '\n' + ''.join(lines))

def remove_hosts(hosts):
    """
    Remove `hosts` from /etc/hosts.
    """
    lines = []
    with open('/etc/hosts', 'r') as f:
        for line in f:
            found = False
            for host in hosts.values():
                pattern = re.compile(HOST_PATTERN.format(host))
                if re.match(pattern, line):
                    found = True
            if not found:
                lines.append(line)
    with open('/etc/hosts', 'w') as f:
        f.write(''.join(lines))


# flag for whether we can reach the service
responding = True

# routines

def start_mongrel():
    global path
    print('Starting mongrel2.')
    try:
        subprocess.check_call(['m2sh', 'start', '-name', 'main'], 
                               cwd=path,
                               stdout=open('/dev/null', 'w'),
                               stderr=open('/dev/null', 'w'))
    except subprocess.CalledProcessError as e:
        print("Mongrel2 couldn't start.")


def kill_mongrel_with_pid(pidpath):
    try:
        print("Checking to make sure mongrel2 wasn't orphaned.")
        with open(pidpath) as f:
            pid = int(f.read())
            os.kill(pid, signal.SIGTERM)
            print("Orphaned mongrel2 with pid {0} has been killed.".format(pid))
    except (IOError, OSError) as e:
        print("It wasn't.")


def load_mongrel():
    global path
    
    # generate mongrel2 config using config func, returning paths
    config_path, db_path = m2()

    # load the config using m2sh
    print('Loading mongrel2 conf into db.')
    try:
        subprocess.check_call(['m2sh', 'load', 
                               '--config', config_path, 
                               '--db', db_path],
                                cwd=path)
    except subprocess.CalledProcessError as e:
        print('Errno {0} loading mongrel2 config.'.format(e.returncode))
        raise M2LoadException(e.returncode)


def live_reload_mongrel():
    # TODO, give m2 an API, starting with `reload`
    global control_port


def send_checkup():

    def checkup_timeout():
        global service, responding, timeout

        timeout.stop()
        if not responding:
            # we've timed out, restart
            print('Mongrel2 not responding, attempting restart.')
            
            # since control port isn't responding, do a dirty kill just in case
            kill_mongrel_with_pid(M2_PID_PATH)
            # start her back up
            # TODO: add configurable delay here before starting again
            start_mongrel()

    def recv_response(msg):
        global responding
        responding = True

    # access globals
    global timeout, control_port, responding

    # listen for ping back
    control_port.on_recv(recv_response)

    # do what's needed to rescue on timeout
    responding = False
    timeout = DelayedCallback(checkup_timeout, CHECKUP_TIMEOUT, io_loop=loop)
    timeout.start()

    # send status request
    control_port.send(tnetstrings.dump(['status', {'what': 'net'}]))


def start(root):

    global loop, path, checkup_periodic, control_port
    path = root

    print('Starting.')

    # add HOSTS to /etc/hosts
    add_hosts(HOSTS)

    # define context
    ctx = zmq.Context()

    # create ioloop
    loop = zmq.eventloop.ioloop.IOLoop()

    # connect req to mongrel2 control port
    c = ctx.socket(zmq.REQ)
    c.connect(M2_CONTROL_PORT)
    control_port = ZMQStream(c, io_loop=loop)

    # define 'checkup' interval
    checkup_periodic = PeriodicCallback(send_checkup, 
                                        CHECKUP_INTERVAL, 
                                        io_loop=loop)

    # load mongrel2 config
    load_mongrel()

    # kill PID if server didn't get shut down at close of last run
    kill_mongrel_with_pid(M2_PID_PATH)

    # start mongrel2 with m2sh
    start_mongrel()

    # start the loop
    checkup_periodic.start()
    loop.start()


def stop(signum, frame):
    global loop, checkup_periodic, control_port, stop_timeout

    def stop_timeout():
        print('Terminate request timed out, mongrel2 might be orphaned.')
        kill_mongrel_with_pid(M2_PID_PATH)
        shutdown()

    def terminate_resp(msg):
        print('Mongrel2 control port confirmed SIGTERM sent.')
        shutdown()

    def shutdown():
        print('Shutting down.')
        remove_hosts(HOSTS)
        control_port.close()
        loop.stop()

    print('\nStopping.')

    # make sure checkup doesn't happen during termination
    checkup_periodic.stop()

    # register terminate response callback
    control_port.on_recv(terminate_resp)

    # prepare timeout
    stop_timeout = DelayedCallback(stop_timeout, STOP_TIMEOUT, io_loop=loop)
    stop_timeout.start()

    # send terminate request
    control_port.send(tnetstrings.dump(['terminate', {}]))


def main():
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    root = os.getcwd()
    start(root)


if __name__ == '__main__':
    main()
