import sys
import pymongo

from config import DB_PORT
from auth import gen_hexdigest

if __name__ == '__main__':
    c = connection = pymongo.Connection('localhost', DB_PORT)
    db = c.auth
    users = db.users
    algorithm, salt, encrypted_pswd = gen_hexdigest(str(sys.argv[2]))
    users.insert({
        'username': sys.argv[1],
        'salt': salt,
        'pswd': encrypted_pswd,
        'algorithm': algorithm
    })