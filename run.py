#!/usr/bin/python

import os, sys, subprocess, signal, time
import hashlib, urllib2, threading, errno

import zmq
from zmq.eventloop.ioloop import PeriodicCallback, DelayedCallback
from zmq.eventloop.zmqstream import ZMQStream


# constants

CHECK_INTERVAL = 1000
CHECKUP_INTERVAL = CHECK_INTERVAL * 2
CHECKUP_TIMEOUT = CHECKUP_INTERVAL / 2
PAUSE_BEFORE_RESTART = CHECK_INTERVAL / 6
PAUSE_AFTER_RESTART = PAUSE_BEFORE_RESTART


# helpers

def create_checksums(root, 
                     nosync=None, 
                     allowed_exts=None, 
                     max_filesize=500 * 1024):
    """
    Walk `root` and return a hash of `path`:`checksum` pairs 
    for each file sized under `max_filesize` that's not inside the 
    `nosync` list and has an extension in the `allowed_exts` list.
    """
    checksums = {}
    for path, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in nosync]
        for f in files:
            full_path = os.path.join(path, f)
            root_path = full_path.split(root)[-1].lstrip('/')
            r, ext = os.path.splitext(f)
            if ext in allowed_exts:
                if os.stat(full_path).st_size < max_filesize:
                    try:
                        with open(full_path, 'rb') as d:
                            sha = hashlib.sha1()
                            key = "{filepath} {size}\0".format(
                                filepath=root_path, 
                                size=os.path.getsize(full_path))
                            sha.update(key)
                            sha.update(d.read())
                            checksums[root_path] = sha.hexdigest()
                    except IOError as e:
                        raise FileOpenException(format(s=root_path))
    return checksums

class DictDiffer(object):
    """
    Calculate the difference between two dictionaries as:
    (1) items added
    (2) items removed
    (3) keys same in both but changed values
    (4) keys same in both and unchanged values
    """
    def __init__(self, current_dict, past_dict):
        self.current_dict, self.past_dict = current_dict, past_dict
        self.set_current = set(current_dict.keys())
        self.set_past = set(past_dict.keys())
        self.intersect = self.set_current.intersection(self.set_past)
    def added(self):
        return self.set_current - self.intersect 
    def removed(self):
        return self.set_past - self.intersect 
    def changed(self):
        return set(o for o in self.intersect 
                   if self.past_dict[o] != self.current_dict[o])
    def unchanged(self):
        return set(o for o in self.intersect 
                   if self.past_dict[o] == self.current_dict[o])

def get_diff(new, old):
    diff = DictDiffer(new, old)
    return diff.added().union(diff.changed()), diff.removed()

def check(path, exts):
    return create_checksums(path,
                            nosync=['.git'],
                            allowed_exts=exts)

def print_output(frames):
    print(frames[0])


# flag for whether we can reach the service
responding = False

# routines

def launch_service():
    global path, service

    try:
        service.terminate()
        print("{0} wasn't dead, but is now.".format(MODULE))
    except Exception as e:
        pass

    with open(os.devnull, 'w') as out:
        return subprocess.Popen(CONFIG['command'], 
                                cwd=path,
                                env=CONFIG['env'],
                                stdout=out,
                                stderr=out)

def send_checkup():

    def checkup_timeout():
        global service, responding, timeout

        timeout.stop()
        if not responding:
            # we've timed out, restart
            # TODO: provide config var for how many times to attempt start before exiting
            print('{0} not responding, attempting start.'.format(MODULE))
            service = launch_service()

    def recv_checkup(msg):
        global responding
        responding = True
        #print(msg)


    def checkup_sent(msg, status):
        pass
        #print(msg, status)

    #print('sending checkup')
    
    # access globals
    global timeout, checkup, responding

    # listen for ping back
    checkup.on_recv(recv_checkup)
    checkup.on_send(checkup_sent)

    # do what's needed to rescue on timeout
    responding = False
    timeout = DelayedCallback(checkup_timeout, CHECKUP_TIMEOUT, io_loop=loop)
    timeout.start()

    # send ping
    checkup.send('You alive?')
    #print('checkup send called')


def restart_service():
    global service

    def restart_checkup():
        global checkup_periodic
        checkup_periodic.start()

    global loop
    checkup_restart = DelayedCallback(restart_checkup, 
                                      PAUSE_AFTER_RESTART, 
                                      io_loop=loop)
    service = launch_service()
    checkup_restart.start()



def check_for_change():

    global checkup, loop, checksums, responding, checkup_periodic

    curr_sums = check(path, watch)
    changed, deleted = get_diff(curr_sums, checksums)
    
    if len(changed) != 0 or len(deleted) != 0:
        checksums = curr_sums
        print('restarting {0}.'.format(MODULE))
        checkup_periodic.stop()
        suicide.send('die please.')
        delay = DelayedCallback(restart_service, 
                                PAUSE_BEFORE_RESTART, 
                                io_loop=loop)
        delay.start()


def start(root):
    """
    Starts main change watching loop.
    """

    global loop, service, path, suicide, checkup, out 
    global checksums, watch, check_periodic, checkup_periodic
    path = root

    print('Starting.')

    # define values for config vars
    try:
        command = CONFIG['command']
        suicide = CONFIG['suicide']
        stdout = CONFIG['stdout']
        checkup = CONFIG['checkup']
        
        try:
            watch = CONFIG['watch']
        except KeyError as e:
            watch = ['.py', '.js', '.php', '.rb']

        try:
            env = CONFIG['env']
        except KeyError as e:
            env = {}

    except KeyError as e:
        print('''You must include CONFIG global in {0}.
Keys `command`, `suicide`, `checkup` and `stdout` are required. 

example:

CONFIG = {
    'command': ['python', 'service.py', './static'],
    'env': {'VAR1': 'abc', 'VAR2': 'xyz'},
    'suicide': 'tcp://127.0.0.1:7004',
    'checkup': 'tcp://127.0.0.1:7005',
    'stdout': 'tcp://127.0.0.1:7006'
}
'''.format(MODULE))
        sys.exit(1)

    # define context
    ctx = zmq.Context()

    # create ioloop
    loop = zmq.eventloop.ioloop.IOLoop()

    # bind suicide address
    s = ctx.socket(zmq.PUB)
    s.bind(suicide)
    suicide = ZMQStream(s, io_loop=loop)

    # bind req to checkup
    c = ctx.socket(zmq.REQ)
    c.bind(checkup)
    c.hwm = 1
    checkup = ZMQStream(c, io_loop=loop)

    # bind a sub to stdout address
    o = ctx.socket(zmq.SUB)
    o.setsockopt(zmq.SUBSCRIBE, '')
    o.bind(stdout)
    out = ZMQStream(o, io_loop=loop)
    out.on_recv(print_output)

    # define 'check for change' interval
    check_periodic = PeriodicCallback(check_for_change, 
                                      CHECK_INTERVAL, 
                                      io_loop=loop)

    # define 'checkup' interval
    checkup_periodic = PeriodicCallback(send_checkup, 
                                        CHECKUP_INTERVAL, 
                                        io_loop=loop)

    # start service 
    service = launch_service()

    # build filesystem state
    checksums = check(path, watch)

    # start the loop
    check_periodic.start()
    checkup_periodic.start()
    loop.start()


def stop(signum, frame):

    # clean up once suicide msg is sent
    def sent(msg, status):
        global loop, suicide, checkup, out
        try:
            suicide.close()
            checkup.close()
            out.close()
            loop.stop()
        except Exception as e:
            print("Couldn't stop IO loop.")
            sys.exit(1)

    global loop, suicide

    print('\nStopping.')

    suicide.on_send(sent)
    suicide.send('die please.')


def main():
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    root = os.getcwd()
    start(root)


if __name__ == '__main__':

    try:
        MODULE = sys.argv[1]
        CONFIG = __import__(MODULE).__dict__['CONFIG']

    except IndexError as e:
        print('You must pass the name of the module to run. Example: ',
              '> python run.py service')
        sys.exit(1)

    except ImportError as e:
        print(e)
        print('could not import `{0}` module to run.'.format(MODULE))
        sys.exit(1)

    except KeyError as e:
        print('module `{0}` does not assign a CONFIG global.'.format(MODULE))
        sys.exit(1)

    main()
