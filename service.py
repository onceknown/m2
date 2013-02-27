"""
This is an example service running behind Mongrel2.  It's written 
without any python abstractions so it can remain flexible and work 
as a readable reference for other languages. 

It demonstrates a hello world app server using MongoDB for persistance.
It's using zmq.green and gevent's patch_socket to optimize IO.

**The ZMQ topology amounts to this:**

SUB on a command address.  For security, all msgs must include a KEY
that matches the KEY passed at service startup. Each service will
define an API for mutating its runtime state. Infrastructure processes
can use this API to modify state realtime as they react to output logs 
from the out channel and state data collected from the checkup channel
(described below).

REP on a checkup address. This provides the heartbeat between infrastructure
processes and app processes.  The goal is to make this channel the 
interface for monitoring your app.  Each service will define an API
for getting information about its status. I'm thinking a config 
management rig running in the spirit of CFEngine3's cf-agent can verify 
the state of a heterogenous cluster of services and repair state using each 
service's command channel.

PUB on an out address. This is how your service logs its state changes.
Each service defines a list of event categories it will broadcast, 
allowing infrastructure processes to SUB just to log sequences that are 
relevant to their purposes. Monitoring processes can be written to
trigger based on defined log sequences and begin a repair cycle.

All other ZMQ channels will be app specific. This example accepts requests
from Mongrel2 and authenticates with an auth service over a REQ/REP socket, 
redirecting to the login url returned by the auth service if not authenticated.

My first instinct is to abstract with a delegate controller approach 
similiar to Cocoa's UI delegates. For this example, I imagine something like

::

class AuthenticatedWebServer(m2.Service):

    def __init__(delegate):
        # body of init() below

::

class Delegate(object):

    def handle_request(path, method, *args, **kwargs)
        "as an example, called to generate response."

>>> AuthenticatedWebServer(Delegate())


AuthenticatedWebServer will use the delegate's methods and attributes
inside its server loop to provide hooks for customization. m2.Service
will guarantee that all subclasses at least implement the ZMQ topology
described above. 

"""

import sys, time, uuid, random, datetime, json
import traceback, urllib, urllib2, Cookie

try:
    # import lib dependencies
    from gevent import monkey; monkey.patch_socket()
    import pymongo
    import zmq.green as zmq
    from mongrel2 import handler
except ImportError as e:
    print('You must have gevent, pymongo, pyzmq, and mongrel2 installed.')
    sys.exit(1)

try:
    # import utils from config.py
    from config import URL_TEMPLATE, Out, db as get_db
except ImportError as e:
    print('You must define an Out class in config.py')
    sys.exit(1)

try:
    # import auth req address
    from auth import VALIDATE as AUTH
except ImportError as e:
    print(e)
    print("Make sure you have auth.py installed.")

try:
    # import linger length
    from run import PAUSE_BEFORE_RESTART as LINGER
except ImportError as e:
    LINGER = 250


# define config for run.py
CONFIG = {
    'service': ['python', 'service.py'],
    'env': {'VAR1': 'abc', 'VAR2': 'xyz'},
    'command': 'tcp://127.0.0.1:7004',
    'checkup': 'tcp://127.0.0.1:7005',
    'out': 'tcp://127.0.0.1:7006'
}

# define m2 endpoints
M2IN = 'tcp://127.0.0.1:7002'
M2OUT = 'tcp://127.0.0.1:7003'



# helpers

markup = '''
<html>
    <head>
        <title>Hello {msg}</title>
    </head>
    <body style="width: 960px; margin: 0 auto;">
        <p style="text-align: center; margin-top: 150px; font-family: Helvetica;">Hello {msg}.</p>
    </body>
</html>
'''

def parse_request(req):
    time = json.dumps(datetime.datetime.now(), default=dthandler)
    req = json.dumps({
        'status': 'RECEIVED',
        'path': req.path,
        'time': time
    })
    return req

def dthandler(obj): 
    return obj.isoformat() if isinstance(obj, datetime.datetime) else None


# server

def init():

    # make zmq connections
    ctx = zmq.Context()

    # sub to SUICIDE address
    command = ctx.socket(zmq.SUB)
    command.linger = LINGER
    command.setsockopt(zmq.SUBSCRIBE, '')
    command.connect(CONFIG['command'])

    # connect to CHECKUP rep address
    checkup = ctx.socket(zmq.REP)
    checkup.linger = LINGER
    checkup.connect(CONFIG['checkup'])

    # connect to STDOUT pub address
    output = ctx.socket(zmq.PUB)
    output.linger = LINGER
    output.hwm = 20
    output.connect(CONFIG['out'])
    out = Out(output, **CONFIG)

    # connect to auth
    auth = ctx.socket(zmq.REQ)
    auth.linger = LINGER
    auth.hwm = 1
    auth.connect(AUTH)

    # connect to m2
    sender_id = uuid.uuid4().hex 
    m2 = handler.Connection(sender_id, M2IN, M2OUT)

    # make mongo connection
    db = None
    try:
        db = get_db(pymongo)
    except Exception as e:
        # wait before dying so run.py has a chance to start checkups
        out.send("Couldn't connect to Mongo at startup.")
        time.sleep(LINGER)
        sys.exit(1)

    # define poller
    poller = zmq.Poller()
    poller.register(command, zmq.POLLIN)
    poller.register(checkup, zmq.POLLIN)
    poller.register(m2.reqs, zmq.POLLIN)

    out.send('HELLO')

    id = uuid.uuid4()

    while True:
        try:
            # wait for IO
            socks = dict(poller.poll())

            # if command PUB comes through
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
                    # clean up sockets
                    command.close()
                    checkup.close()
                    output.close()
                    m2.shutdown()
                    ctx.term()
                    #gevent.shutdown()

                    # die
                    return

            # if a checkup REQ comes through
            if checkup in socks and socks[checkup] == zmq.POLLIN:

                # reply
                msg = checkup.recv()
                checkup.send("yep.")

            # if mongrel2 PUSHes a request
            elif m2.reqs in socks and socks[m2.reqs] == zmq.POLLIN:

                # handle request
                req = m2.recv()

                # if a disconnect, bail
                if req.is_disconnect(): 
                    continue

                # log request
                out.send('REQUEST', parse_request(req))

                # get session from cookie
                session = ''
                cookie = req.headers.get('cookie')
                if cookie:
                    c = Cookie.SimpleCookie(str(cookie))
                    s = c.get('session')
                    if s:
                        session = str(s.value)

                # send auth req
                try:
                    auth.send(session)
                except zmq.ZMQError as e:
                    out.send('ERROR', 'Auth service req/rep in wrong state.')

                    # reset state by closing and reconnecting
                    auth.close()
                    auth = ctx.socket(zmq.REQ)
                    auth.linger = LINGER
                    auth.hwm = 1
                    auth.connect(AUTH)

                    # auth service is down, so 500
                    m2.reply_http(req, 'Auth service not responding', code=500)
                    continue

                # poll with timeout for response
                auth_poller = zmq.Poller()
                auth_poller.register(auth, zmq.POLLIN)
                evts = auth_poller.poll(100)

                # if auth service has responded
                if evts:
                    resp = auth.recv_json()

                    # if we're authed, serve
                    if resp.get('success'):

                        ###########################
                        ## Now do some app logic ##
                        ###########################

                        # grab a random message from mongo
                        try:
                            c = db.messages.count()
                            r = list(db.messages.find())[random.randrange(0, c)]
                        except (pymongo.errors.ConnectionFailure, pymongo.errors.AutoReconnect) as e:
                            # this request can't happen, so 500
                            out.send('DB', json.dumps({
                                'status': 'LOST_CONN',
                                'error': str(e)
                            }))
                            m2.reply_http(req, 'DB connection lost.', code=500, headers={
                                'Content-Type': 'text/html',
                                "Cache-Control": "no-cache, must-revalidate",
                                "Pragma": "no-cache",
                                "Expires": "Sat, 26 Jul 1997 05:00:00 GMT"
                            })
                            continue


                        # insert data into markup template
                        if r.get('text'):
                            m = markup.format(msg=r.get('text'))
                        else:
                            m = markup.format(msg='Nobody')

                        # reply with no cache headers
                        m2.reply_http(req, m, headers={
                            'Content-Type': 'text/html',
                            "Cache-Control": "no-cache, must-revalidate",
                            "Pragma": "no-cache",
                            "Expires": "Sat, 26 Jul 1997 05:00:00 GMT"
                        })

                        # log end of request
                        end_time = json.dumps(datetime.datetime.now(), default=dthandler)
                        out.send('REQUEST', json.dumps({
                            'status': 'DELIVERED',
                            'path': req.path,
                            'time': end_time
                        }))

                        ###########################
                        ## app logic is complete ##
                        ###########################

                    # otherwise we do the auth redirect
                    else:
                        auth_url = resp.get('redirect')
                        path = URL_TEMPLATE.rstrip('/').format(req.headers.get('host') 
                                                             + req.headers.get('URI'))

                        # TODO: handle urls that include qs's and hashes
                        path = urllib.quote(path)
                        redirect = str(auth_url + '?redirect=' + path)

                        m2.reply_http(req,
                                      '',
                                      code=302,
                                      headers={
                                            'Location': redirect
                                      })
                else:
                    # reset state by closing and reconnecting
                    out.send('ERROR', 'auth timed out.')
                    auth.close()
                    auth = ctx.socket(zmq.REQ)
                    auth.linger = LINGER
                    auth.hwm = 1
                    auth.connect(AUTH)

                    # auth service is down, so 500
                    m2.reply_http(req, 'Auth service not responding', code=500)
                    continue

                # an unexpected error if we get here, respond 500
                m2.reply_http(req, 'Server Error', code=500)


        # keep server up by catching all exceptions raised from inside server loop
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