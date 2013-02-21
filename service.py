import sys, time, uuid, random, traceback

try:
    from gevent import monkey; monkey.patch_socket()
    import pymongo
    import zmq.green as zmq
    from mongrel2 import handler
except ImportError as e:
    print('You must have gevent, pymongo, pyzmq, and mongrel2 installed.')
    sys.exit(1)

# import output util from config.py
try:
    from config import Out, db as get_db
except ImportError as e:
    print('You must define an Out class in config.py')
    sys.exit(1)

# set linger length
try:
    from run import PAUSE_BEFORE_RESTART as LINGER
except ImportError as e:
    LINGER = 250


# define config for run.py
CONFIG = {
    'command': ['python', 'service.py'],
    'env': {'VAR1': 'abc', 'VAR2': 'xyz'},
    'suicide': 'tcp://127.0.0.1:7004',
    'checkup': 'tcp://127.0.0.1:7005',
    'stdout': 'tcp://127.0.0.1:7006'
}

# define m2 endpoints
M2IN = 'tcp://127.0.0.1:7002'
M2OUT = 'tcp://127.0.0.1:7003'


# helpers

markup = '''
<html>
    <head>
        <title>Hello World</title>
    </head>
    <body style="width: 960px; margin: 0 auto;">
        <p style="text-align: center; margin-top: 150px; font-family: Helvetica;">Hello {msg}.</p>
    </body>
</html>
'''

def parse_request(req):
    return '{uri}'.format(uri=req.headers['URI'])


# server

def main():

    # make zmq connections
    ctx = zmq.Context()

    # sub to SUICIDE address
    suicide = ctx.socket(zmq.SUB)
    suicide.linger = LINGER
    suicide.setsockopt(zmq.SUBSCRIBE, '')
    suicide.connect(CONFIG['suicide'])

    # connect to CHECKUP rep address
    checkup = ctx.socket(zmq.REP)
    checkup.linger = LINGER
    checkup.connect(CONFIG['checkup'])

    # connect to STDOUT pub address
    output = ctx.socket(zmq.PUB)
    output.linger = LINGER
    output.hwm = 20
    output.connect(CONFIG['stdout'])
    out = Out(output, **CONFIG)

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
    poller.register(suicide, zmq.POLLIN)
    poller.register(checkup, zmq.POLLIN)
    poller.register(m2.reqs, zmq.POLLIN)

    while True: 
        try:
            
            # wait for IO
            socks = dict(poller.poll())

            # if suicide PUB comes through
            if suicide in socks and socks[suicide] == zmq.POLLIN:

                suicide.recv()

                # clean up sockets
                suicide.close()
                checkup.close()
                output.close()
                m2.shutdown()
                gevent.shutdown()

                # die please
                return

            # if a checkup REQ comes through
            if checkup in socks and socks[checkup] == zmq.POLLIN:

                # reply
                checkup.recv()
                checkup.send("yep.")

            # if mongrel2 PUSHes a request
            elif m2.reqs in socks and socks[m2.reqs] == zmq.POLLIN:

                # handle request
                req = m2.recv()

                # if a disconnect, bail
                if req.is_disconnect(): 
                    continue

                # log request
                out.send(parse_request(req))

                ###########################
                ## Now do some app logic ##
                ###########################

                # grab a random message from mongo
                try:
                    c = db.messages.count()
                    r = list(db.messages.find())[random.randrange(0, c)]
                except (pymongo.errors.ConnectionFailure, pymongo.errors.AutoReconnect) as e:
                    # this request can't happen, so 500
                    out.send('Lost connection with Mongo.')
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

                ###########################
                ## app logic is complete ##
                ###########################


        # keep server up by catching all exceptions raised from inside server loop
        except Exception as e:
            out.send('\nFAIL!\n-----')
            out.send('{0}----'.format(traceback.format_exc()))


if __name__ == '__main__':

    # start up
    main()