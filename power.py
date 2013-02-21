import os, sys, subprocess, signal, errno
from config import PATHS, DB_PORT, USER


START = 'on'
STOP = 'off'
STATUS = 'status'

def generate_dirs(root, paths):
    for key, path in paths.items():
        try:
            os.makedirs(os.path.join(root, path))
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise e

def start(root, pid):

    # add dirs if necessary
    generate_dirs(root, PATHS)

    # generate conf
    svtemplate = os.path.join(root, 'supervisor.tpl')
    svconf = os.path.join(root, 'supervisor.conf')
    try:
        with open(svtemplate, 'r') as tpl:
            template = tpl.read()
            with open(svconf, 'w') as conf:
                conf.write(template.format(**{
                    'RUN_PATH': os.path.join(root, PATHS['RUN']),
                    'LOG_PATH': os.path.join(root, PATHS['LOGS']),
                    'DB_PATH': os.path.join(root, PATHS['DB']),
                    'DB_PORT': DB_PORT,
                    'ROOT': root,
                    'USER': USER,
                    'PID': pid
                }))
    except IOError as e:
        raise e
    try:
        # start up the daemon
        subprocess.check_call(['supervisord',  '-c', './supervisor.conf'], 
                         cwd=root)
    except subprocess.CalledProcessError as e:
        print(e)

def stop(root, pid):
    try:
        subprocess.check_call(['supervisorctl',  '-c', './supervisor.conf', 
                               'stop', 'all'], 
                              cwd=root)
    except subprocess.CalledProcessError as e:
        print('Gentle stop failed so death by SIGTERM.')
    try:
        with open(pid) as f:
            pid = int(f.read())
            os.kill(pid, signal.SIGTERM)
    except (OSError, IOError) as e:
        print('No pid to kill.')

def status(root, pid):
    try:
        stats = str(subprocess.check_output(['supervisorctl', 
                                             '-c', './supervisor.conf', 
                                             'status'], 
                                            cwd=root))
        return stats.rstrip('\n').split('\n')
    except subprocess.CalledProcessError as e:
        return ['Status failed. Try `{START}` command.'.format(START=START)]

def main(cmd):
    root = os.getcwd()
    pid = os.path.join(root, PATHS['RUN'], 'supervisor.pid')
    if cmd == START:
        stop(root, pid)
        start(root, pid)
        print("We're up.")
        sys.exit(0)
    elif cmd == STOP:
        stop(root, pid)
        print("We're down.")
        sys.exit(0)
    elif cmd == STATUS:
        stats = status(root, pid)
        print('---------------------------------STATUS-------------------------------')
        print('----------------------------------------------------------------------')
        for stat in stats:
            print(stat)
            print('----------------------------------------------------------------------')
        sys.exit(0)



if __name__ == '__main__':
    try:
        cmd = sys.argv[1]
        if cmd in (START, STOP, STATUS):
            main(cmd)
        else:
            print('''power.py is `{START}` or `{STOP}`, and you can get `{STATUS}`.
Usage: python power.py {START}|{STOP}|{STATUS}'''.format(START=START, STOP=STOP, 
                                                  STATUS=STATUS))
            sys.exit(1)
    except IndexError as e:
        main(START)