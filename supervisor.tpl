[unix_http_server]
file={RUN_PATH}/supervisor.sock

[supervisord]
logfile={LOG_PATH}/supervisor.log
logfile_maxbytes=50MB
logfile_backups=10
loglevel=info
pidfile={PID}
umask=2

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix://{RUN_PATH}/supervisor.sock
umask=2
autostart=true
autorestart=true
stdout_logfile={LOG_PATH}/supervisorctlout.log
stdout_logfile_maxbytes=1MB 
stdout_logfile_backups=10
stderr_logfile={LOG_PATH}/supervisorctlerr.log
stderr_logfile_maxbytes=1MB 
stderr_logfile_backups=10


[program:mongo]
command=mongod -port {DB_PORT} --dbpath={DB_PATH}
priority=1
process_name=mongo
numprocs=1
directory={ROOT}
umask=2
autostart=true
autorestart=true
startsecs=3
startretries=3
stopsignal=INT
stopwaitsecs=3
user={USER}
redirect_stderr=false
stdout_logfile={LOG_PATH}/mongout.log
stdout_logfile_maxbytes=1MB
stdout_logfile_backups=10
stdout_capture_maxbytes=1MB
stderr_logfile={LOG_PATH}/mongoerr.log
stderr_logfile_maxbytes=1MB
stderr_logfile_backups=10
stderr_capture_maxbytes=1MB
environment=


[program:m2]
command=python m2.py
priority=2
process_name=m2
numprocs=1
directory={ROOT}
umask=2
autostart=true
autorestart=true
startsecs=3
startretries=3
stopsignal=INT
stopwaitsecs=3
user=root
redirect_stderr=false
stdout_logfile={LOG_PATH}/m2out.log
stdout_logfile_maxbytes=1MB
stdout_logfile_backups=10
stdout_capture_maxbytes=1MB
stderr_logfile={LOG_PATH}/m2err.log
stderr_logfile_maxbytes=1MB
stderr_logfile_backups=10
stderr_capture_maxbytes=1MB
environment=


[program:auth]
command=python auth.py
priority=3
process_name=auth
numprocs=1
directory={ROOT}
umask=2
autostart=true
autorestart=true
startsecs=3
startretries=3
stopsignal=INT
stopwaitsecs=3
user={USER}
redirect_stderr=false
stdout_logfile={LOG_PATH}/auth-out.log
stdout_logfile_maxbytes=1MB
stdout_logfile_backups=10
stdout_capture_maxbytes=1MB
stderr_logfile={LOG_PATH}/auth-err.log
stderr_logfile_maxbytes=1MB
stderr_logfile_backups=10
stderr_capture_maxbytes=1MB
environment=

[program:service]
command=python service.py
process_name=service
numprocs=1
directory={ROOT}
umask=2
autostart=true
autorestart=true
startsecs=3
startretries=3
stopsignal=INT
stopwaitsecs=3
user={USER}
redirect_stderr=false
stdout_logfile={LOG_PATH}/service-out.log
stdout_logfile_maxbytes=1MB
stdout_logfile_backups=10
stdout_capture_maxbytes=1MB
stderr_logfile={LOG_PATH}/service-err.log
stderr_logfile_maxbytes=1MB
stderr_logfile_backups=10
stderr_capture_maxbytes=1MB
environment=

[program:ecommerce]
command=/root/meteor/meteor --production
process_name=ecommerce
numprocs=1
directory={ROOT}/../realestate
umask=2
autostart=true
autorestart=true
startsecs=3
startretries=3
stopsignal=INT
stopwaitsecs=3
user={USER}
redirect_stderr=false
stdout_logfile={LOG_PATH}/ecommerce-out.log
stdout_logfile_maxbytes=1MB
stdout_logfile_backups=10
stdout_capture_maxbytes=1MB
stderr_logfile={LOG_PATH}/ecommerce-err.log
stderr_logfile_maxbytes=1MB
stderr_logfile_backups=10
stderr_capture_maxbytes=1MB
environment=MONGO_URL="mongodb://127.0.0.1:{DB_PORT}/ecommerce", URL="http://ecommerce.dannydavidson.com"

