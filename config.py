import os

# helper template
URL_TEMPLATE = 'http://{0}/'

# define the default host
FQDN = 'dannydavidson.com'

# map names to hosts
HOSTS = {
    'root_host': FQDN,
    'www_host': 'www.{0}'.format(FQDN),
    'login_host': 'auth.{0}'.format(FQDN)
}

# define root and login url used by auth.py
HOME_URL = URL_TEMPLATE.format(HOSTS['root_host'])
LOGIN_URL = URL_TEMPLATE.format(HOSTS['login_host'])

# set web server port
PORT = 80

# set mongo port
DB_PORT = 27017

# set user to run servers
USER = 'root'

# map the dirs to constants so dir names can change without affecting code
PATHS = {
    'RUN': 'run', 
    'TEMP': 'tmp', 
    'LOGS': 'logs', 
    'DB': 'db',
    'DUMP': 'dump'
}

# set where to write pid file
M2_PID_PATH = os.path.join(os.getcwd(), PATHS['RUN'], 'mongrel2.pid')


import logging
from logging.handlers import RotatingFileHandler
class Out(object):
    """
    An abstraction for sending output.  `send` prints to stdout,
    logs using python's logging tools, and PUBs on the process's
    output socket. Obviously, as I dial in on the API this will be
    overkill, but for now it gets log messages out whether you're 
    running standalone, from run.py, or from supervisord.
    """

    def __init__(self, out_sock, **kwargs):

        # assign properties
        self.sock = out_sock
        self.config = kwargs

        # clean up service so it can work as a log filename
        command = kwargs.get('service', 'unknown')
        cleaned_command = '_'.join(command) + '.log'
        cleaned_command = cleaned_command.strip(' \\-')

        # set up the logger
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.DEBUG)
        h = RotatingFileHandler(os.path.join(os.getcwd(), 
                                    PATHS['LOGS'], 
                                    cleaned_command),
                                backupCount=3,
                                maxBytes=1024 * 100000)
        h.setLevel(logging.DEBUG)
        self.logger.addHandler(h)

    def send(self, key, msg=''):
        format = '{0} {1}'
        print(format.format(key, msg))
        self.sock.send(format.format(key, str(msg)))
        self.logger.debug(format.format(key, msg))

def db(pymongo):
    mongo = pymongo.MongoClient('localhost',
                                DB_PORT,
                                use_greenlets=True)
    return mongo.hello


def m2():
    """
    Generates mongrel2.conf and returns config options needed by m2sh.
    This should be all done through a ZMQ API in m2.py, but this hack
    works for now.
    """
    m2_conf = os.path.join(os.getcwd(), 'mongrel2.conf')
    with open(os.path.join(os.getcwd(), 'mongrel2.tpl'), 'r') as t:
        template = t.read()
        config = template.format(PORT=PORT, **HOSTS)
        with open(m2_conf, 'w') as c:
            c.write(config)
    return (m2_conf, os.path.join(os.getcwd(), 'config.sqlite'))
