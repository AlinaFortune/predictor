# -*- coding: utf-8 -*-
import os
import subprocess
import time

__author__ = 'stsouko'
from modelset import register_model


class Model():
    def getdesc(self):
        desc = 'model generate sorted conformers'
        return desc

    def getname(self):
        name = 'best conformers'
        return name

    def getexample(self):
        return ' '

    def is_reation(self):
        return 0

    def gethashes(self):
        hashlist = []
        return hashlist

    def getresult(self, chemical):
        file_name = int(time.time())
        try:
            if not os.path.exists('/home/server/conf/%d' % file_name):
                os.makedirs('/home/server/conf/%d' % file_name)
            with open('/home/server/conf/%d/temp.mrv' % file_name, 'w') as f:
                f.write(chemical['structure'])

            subprocess.call("ssh timur@130.79.41.90 /home/timur/server/start %d" % file_name, shell=True)
            subprocess.call("mv /home/server/conf/%d/result.zip /home/server/download/%d.zip" % (file_name, file_name), shell=True)

            result = [dict(type='link', attrib='file with archive', value='download/%d.zip' % file_name)]
        except:
            result = False
        return result


model = Model()
register_model(model.getname(), Model)
