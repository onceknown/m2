#!/usr/bin/python

"""
This is a "basic auth" service, created to demonstrate a couple things.

First, many frameworks come with auth baked in, but each one implements
a little differently and it's often difficult to get multiple frameworks
working together.  With a self-contained service like this one, you can add
new apps written in any language and they just need to implement the auth
REQ/REP protocol to plug in.

Second, I want to demonstrate how powerful it is when you can accept both
HTTP and ZMQ streams in the same running process.  The ZMQ API is exposed 
internally, while the mongrel2 handler provides the external interface to
the user. 

"""

import os, sys, hashlib, urllib2, traceback, json, random
from cgi import parse_qs
from Cookie import SimpleCookie
from uuid import uuid4
from datetime import datetime

try:
    # import lib dependencies
    from gevent import monkey; monkey.patch_socket()
    import pymongo
    import zmq.green as zmq
    from mongrel2 import handler
    import bcrypt
except ImportError as e:
    print('You must have gevent, pymongo, pyzmq, bcrypt and mongrel2 installed.')
    sys.exit(1)


# import config constants and util funcs
try:
    from config import Out, PATHS, DB_PORT, LOGIN_URL, HOME_URL, FQDN
    from run import PAUSE_BEFORE_RESTART as LINGER
except ImportError as e:
    raise e


# define config for run.py
CONFIG = {
    'service': ['python', 'auth.py'],
    'env': {},
    'command': 'tcp://127.0.0.1:7007',
    'checkup': 'tcp://127.0.0.1:7008',
    'out': 'tcp://127.0.0.1:7009',
    'watch': ['.py', '.html']
}

# define m2 and auth validation addresses
M2IN = "tcp://127.0.0.1:7010"
M2OUT = "tcp://127.0.0.1:7011"
VALIDATE = "tcp://127.0.0.1:7012"


# helpers

HISTORY_FORMAT='%Y-%m-%d_%H:%M:%S:%f'

cookie_template = 'session={s}; Domain=.{fqdn}; Max-Age=86400;'

error_template = '<p class="alert alert-error"><span class="icon-exclamation-sign"></span> Invalid username or password.</p>'


def dthandler(obj): 
    return obj.isoformat() if isinstance(obj, datetime) else None


# borrowed from brubeck
BCRYPT = 'bcrypt'
def gen_hexdigest(raw_password, algorithm=BCRYPT, salt=None):
    """
    Takes the algorithm, salt and password and uses Python's
    hashlib to produce the hash. Currently only supports bcrypt.
    """
    if raw_password is None:
        raise ValueError('No empty passwords!')
    if algorithm == BCRYPT:
        # bcrypt has a special salt
        if salt is None:
            salt = bcrypt.gensalt()
        return (algorithm, salt, bcrypt.hashpw(raw_password, salt))
    raise ValueError('Unknown password algorithm')


def _lscmp(a, b):
    """
    Compares two strings in a cryptographically safe way
    """
    return not sum(0 if x == y else 1 for x, y in zip(a, b)) \
           and len(a) == len(b)


# startup

def init():

    # make zmq connections
    ctx = zmq.Context()

    # sub to SUICIDE address
    command = ctx.socket(zmq.SUB)
    command.setsockopt(zmq.SUBSCRIBE, '')
    command.linger = LINGER
    command.connect(CONFIG['command'])

    # connect to CHECKUP rep address
    checkup = ctx.socket(zmq.REP)
    checkup.linger = LINGER
    checkup.connect(CONFIG['checkup'])

    # connect to OUT pub address
    output = ctx.socket(zmq.PUB)
    output.linger = LINGER
    output.hwm = 100
    output.connect(CONFIG['out'])
    out = Out(output, **CONFIG)

    # connect to m2
    sender_id = uuid4().hex 
    m2 = handler.Connection(sender_id, M2IN, M2OUT, LINGER)

    # bind to validate address
    validate = ctx.socket(zmq.REP)
    validate.linger = LINGER
    validate.bind(VALIDATE)

    # define poller
    poller = zmq.Poller()
    poller.register(command, zmq.POLLIN)
    poller.register(checkup, zmq.POLLIN)
    poller.register(validate, zmq.POLLIN)
    poller.register(m2.reqs, zmq.POLLIN)

    # connect to mongo
    try:
        c = connection = pymongo.Connection('localhost', DB_PORT)
        db = c.auth
        users = db.users
        sessions = db.sessions
    except Exception as e:
        out.send("DB", "Couldn't connect to MongoDB.")

    # cache login page template
    with open('./login.html', 'r') as f:
        login_template = f.read()

    out.send('HELLO')

    # start server loop
    while True: 
        try:
            socks = dict(poller.poll())

            # command published
            if command in socks and socks[command] == zmq.POLLIN:

                msg = command.recv_json()

                # log and ignore messages that don't validate
                if msg.get('key') != KEY:
                    out.send('SECURITY', json.dumps({
                        'status': 'WRONG_KEY',
                        'msg': msg,
                        'id': str(id)
                    }))
                    continue

                if msg.get('command') == 'die':

                    out.send('GOODBYE')

                    # close all sockets
                    validate.close()
                    command.close()
                    checkup.close()
                    output.close()
                    m2.shutdown()
                    ctx.term()
                    gevent.shutdown()

                    # die
                    return


            # checkup request made
            elif checkup in socks and socks[checkup] == zmq.POLLIN:

                # reply
                checkup.recv()
                checkup.send("yep.")
                continue


            # session validation request made
            elif validate in socks and socks[validate] == zmq.POLLIN:

                # return validation outcome
                session_id = validate.recv()
                session = sessions.find_one({'key': session_id})
                if session:
                    out.send('VALIDATE', '{0} successfully validated.'.format(session_id))
                    validate.send_json({'success': True})
                    continue
                out.send('VALIDATE', '{0} did not validate.'.format(session_id))
                validate.send_json({'success': False, 'redirect': LOGIN_URL})
                continue


            # push from webserver
            elif m2.reqs in socks and socks[m2.reqs] == zmq.POLLIN:

                req = m2.recv()

                username, pswd = None, None
                redirect = HOME_URL

                # if posting login creds
                if req.headers.get('METHOD') == 'POST':

                    # parse creds
                    d = parse_qs(req.body)
                    try:
                        username, pswd = d.get('name')[0], d.get('password')[0]
                        redirect = d.get('redirect')
                        redirect = redirect[0] if len(redirect) else ''
                        if redirect:
                            redirect = redirect.lstrip('/')
                            redirect = urllib2.unquote(redirect)
                    except (KeyError, IndexError, TypeError) as e:
                        out.send('LOGIN', json.dumps({
                            'status': 'BAD_POST_DATA',
                            'error': e,
                            'id': req.conn_id
                        }))

                    out.send('LOGIN', json.dumps({
                        'status': 'LOGIN_POST',
                        'username': username,
                        'redirect': redirect,
                        'id': req.conn_id
                    }))

                    # if creds were sent
                    if username and pswd:
                        user = users.find_one({'username': username})

                        # if user validates set session cookie and redirect
                        if user:
                            algorithm, salt, encrypted_pswd = gen_hexdigest(pswd, 
                                                              salt=user.get('salt'))
                            
                            if _lscmp(encrypted_pswd, user.get('pswd')):
                                timestamp = datetime.now().strftime(HISTORY_FORMAT)
                                h = hashlib.sha512('{random}{time}{user}'.format(
                                                        random=random.randint(0, 100 * 100 * 100),
                                                        time=timestamp,
                                                        user=user.get('_id')))
                                value = h.hexdigest()

                                sessions.insert({
                                    'key': value,
                                    'began': timestamp, 
                                    'user_id': user.get('_id') 
                                }, safe=True)

                                cookie_value = cookie_template.format(s=value, 
                                                                      fqdn=FQDN)
                                m2.reply_http(req,
                                              '',
                                              code=302,
                                              headers={
                                                    'Location': redirect,
                                                    'Set-Cookie': cookie_value
                                              })

                                out.send('LOGIN', json.dumps({
                                    'status': 'LOGIN_SUCCESS',
                                    'username': username,
                                    'redirect': redirect,
                                    'id': req.conn_id
                                }))

                                continue

                    # respond with invalid login
                    out.send('LOGIN', json.dumps({
                        'status': 'INVALID_CREDS',
                        'username': username,
                        'id': req.conn_id

                    }))
                    response = login_template.format(
                                    title='Invalid Login',
                                    error=error_template ,
                                    redirect=redirect)

                    m2.reply_http(req,
                                  response,
                                  code=200,
                                  headers={
                                    'Content-Type': 'text/html'
                                  })
                    continue

                # else get request assumed
                else:

                    code = 200
                    qs = req.headers.get('QUERY')

                    if qs:
                        try:
                            # grab redirect from query string so it can be 
                            # passed to hidden input
                            redirect = parse_qs(qs).get('redirect')[0]
                        except (KeyError, IndexError):
                            redirect = ''

                    start_time = json.dumps(datetime.now(), default=dthandler)
                    out.send('REQUEST', json.dumps({
                        'status': 'RECEIVED',
                        'redirect': redirect,
                        'time': start_time,
                        'id': req.conn_id
                    }))

                    try:
                        # render page
                        response = login_template.format(title="Please Log In",
                                                         error='',
                                                         redirect=redirect)
                    except KeyError as e:
                        out.send('ERROR', str(e))
                        response = "Server Error: Couldn't load auth page."
                        code = 500

                    m2.reply_http(req,
                                  response,
                                  code=code,
                                  headers={
                                      'Content-type': 'text/html'
                                  })

                    end_time = json.dumps(datetime.now(), default=dthandler)
                    out.send('REQUEST', json.dumps({
                        'status': 'DELIVERED',
                        'time': end_time,
                        'id': req.conn_id
                    }))

                    continue
        except Exception as e:
            out.send('\nFAIL!\n-----')
            out.send('{0}----'.format(traceback.format_exc()))


if __name__ == '__main__':

    # record key passed
    try:
        KEY = str(sys.argv[1])
    except IndexError as e:
        KEY = None

    # start up
    init()