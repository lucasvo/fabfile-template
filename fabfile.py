# Usage: fab <instance> <options> <action>
#        e.g. fab devel deploy
#        In case of "No settings_XXX present" ImportError, try setting 
#        "PYTHONPATH" environment variable value to current directory,
#        i.e. "PYTHONPATH=. fab ch deploy"  
#
# Instances: live (default)
#
# Options:	
#  * email: send email after success deployment
#  * force: ignore that server has changes not saved in central repo
#  * nobackup: don't backup DB
#
# Actions:
#  * status: see server status (running/not runnning, whether there's local changes) [Broken]
#  * setup: create instance directories and database
#  * setup_no_db: create instance directories but don't touch database
#  * deploy: checkout latest application version, migrate database
#            and re-link application root to latest version.
#            FCGI server is restarted as well.
#  * backup: create database SQL backup
#  * test: run unit tests on instance application root
#  * server_start: (re)start Django server
#  * virtualenv: set up virtualenv into current application istance
#  * check_no_local_changes: check if there's any changes on server that were not saved in central repo
#  
#  Options
#  * req - if present, requirements.txt in installation directory will be overwritten by local version 
#  * rev:<revision hash> - if present, a "git checkout <revision hash>" coomand will be executed right after cloning the repo 
#  * branch:<branch name> - if present, a "git checkout <branch name>" coomand will be executed right after cloning the repo 

from __future__ import with_statement
import sys
from fabric.api import *
from fabric.contrib.console import confirm
from datetime import datetime
from email.mime.text import MIMEText
import os

# Project data
env.project = "example" # This is the django project name
env.github_user = 'example'
env.github_repo = 'example'
env.branch = 'master'   # branch to checkout

env.dbcreate_user = "example"
env.dbcreate_password = "example"


env.recipients = [''] # An email summary is sent to all recipients in this list
env.from_email = 'Deployment Robot <robot@example.com>' # The sender field of the email summary, use the following format: Deployment Robot <robot@example.com>

# Fill out hosts here if you don't want to pass it as an argument for every deployment
#env.hosts = [
#    '', 
#    ]

# Options
env.use_locales = True # Should we run compilemessages on deploy? 
env.backup = True       # generate DB backup
env.send_email = False  # send notification emails about deployment (It's recommended to set this value by environment)


# Initialize vars
env.changelog = env.actual_revision = env.app_root = env.app_path = '' 
env.previous_revision = env.wsgi_script = env.revision = env.runserver_port = env.pidfile  = None
env.message = '''Deployment Info\n'''
env.force = False
env.repo = "git@github.com:%(github_user)s/%(github_repo)s.git" % env
env.github_link = "https://github.com/%(github_user)s/%(github_repo)s" % env



def live():
    env.env = "live"
    env.app_root =      '/home/www-data/example.com' 
    env.send_email = True
    env.wsgi_script  = 'wsgi/example.com.wsgi'

    # Options
    # env.branch = 'master'
    # env.pidfile = 'server.pid'
    # env.runserver_port = 8080
    # env.hosts = ['root@localhost',] 

def dev():
    env.env = "dev"
    env.app_root =      '/home/www-data/dev.example.com' 
    env.runserver_port = 8001 
    env.send_email = False
    env.pidfile = os.path.join(env.app_root, 'server.pid')

# Begin Deployment script

def nobackup():
    env.backup = False

@runs_once
def rev(r):
    env.revision = r

@runs_once
def branch(branch):
    env.branch = branch

@runs_once
def email():
    env.send_email = True

@runs_once
def force():
    env.force = True
    print "*"*80
    print "You disabled remote host checks so you might overwrite or loose server changes"
    print "*"*80

@runs_once    
def init():
    sys.path.append('.') # make current path visible for import
    exec("import settings_%(env)s as settings" % env)

    env.settings = settings
    env.db_name = env.settings.DATABASES['default']['NAME']
    env.test_db_name = env.settings.DATABASES['default']['TEST_NAME']
    env.db_user = env.settings.DATABASES['default']['USER']
    env.db_password = env.settings.DATABASES['default']['PASSWORD']
    if not hasattr(env, 'timestamp'):  env.timestamp = run('date +"%Y-%m-%d %H:%M:%S"')
    env.version_dir = env.timestamp.replace('-','').replace(':','').replace(' ','_')
    env.version_path = env.app_root+"/"+env.version_dir+"/"+env.project
    env.app_path = env.app_root+"/"+env.project
    env.virtualenv = env.app_root+'/pyenv'

    
@runs_once
def setup():
    init()
    run("mkdir -p %(app_root)s" % env )
    run("mkdir -p %(app_root)s/backups" % env )
    run("mkdir -p %(app_root)s/upload" % env )
    run("mkdir -p %(app_root)s/log" % env)
    repo_setup()
    repo_checkout()
    install_virtualenv()
    db_create()
    link_to_current(setup=True)

@runs_once    
def db_create():
    databases = run("""
                    echo "show databases;" |  mysql -u %(dbcreate_user)s  --password="%(dbcreate_password)s" --raw --batch
                    """ % env)
    databases = databases.split('\n')

    if not env.db_name in databases:
        run("""cd %(version_path)s
            echo "create database %(db_name)s CHARACTER SET utf8;" | mysql -u %(dbcreate_user)s  --password="%(dbcreate_password)s"
            """ % env)
            
    run("""
            %(virtualenv)s/bin/python manage.py syncdb --noinput --settings=settings_%(env)s
            %(virtualenv)s/bin/python manage.py migrate --settings=settings_%(env)s
    """ % env)

@runs_once
def init_test_db():
    run("""
        echo "grant all privileges on %(test_db_name)s.* to '%(db_user)s'@'localhost' identified by '%(db_password)s';" | mysql -u %(dbcreate_user)s  --password="%(dbcreate_password)s"
    """ % env)

@runs_once
def repo_checkout():
    init()
    try:
        env.previous_revision = run("""
            cd %(app_path)s
            git rev-parse HEAD
        """ % env)
    except:
        env.previos_revision = None
    repo_update()
    run(""" mkdir -p %(version_path)s
            cp -r %(app_root)s/repository/%(project)s/.git %(version_path)s/
            cd %(version_path)s
            git fetch origin %(branch)s
            git checkout -f %(branch)s
            git fetch origin
            git merge origin/%(branch)s
            ln -sfn %(app_root)s/log %(version_path)s/log
            rm -f %(version_path)s/settings.pyc
            ln -sfn %(version_path)s/settings_%(env)s.py %(version_path)s/settings.py
        """ % env)
    if env.revision:
        run(""" cd %(version_path)s
                git checkout -f %(revision)s
            """ % env)
    env.actual_revision = run("""
        cd %(version_path)s
        git rev-parse HEAD
        """ % env)

    if env.previous_revision:
        env.changelog = ("%(github_link)s/compare/%(previous_revision)s...%(actual_revision)s\n\n" % env) + run("""
            cd %(version_path)s
            git log --reverse --format='%%ai %%s / %%an' %(previous_revision)s..%(actual_revision)s | tee -a %(app_root)s/CHANGELOG
        """ % env)
    else:
        env.changelog = run("""
            cd %(version_path)s
            git log --reverse --format='%%ai %%s / %%an' -1 %(actual_revision)s | tee -a %(app_root)s/CHANGELOG
        """ % env)

@runs_once
def repo_setup():
    init()
    run("""cd %(app_root)s
        mkdir -p repository
        cd repository
        git clone %(repo)s %(project)s
        """ % env)

def repo_update():
    run("""cd %(app_root)s/repository/%(project)s
        git fetch origin %(branch)s
        """ % env)

@runs_once
def install_virtualenv(current=False):
    env.current_app_root = env.app_path if current else env.version_path
    run("""cd %(app_root)s
            virtualenv %(virtualenv)s 
            %(virtualenv)s/bin/easy_install pip
            %(virtualenv)s/bin/pip install -r %(current_app_root)s/requirements.txt
    """ % env)

def virtualenv():
    init()
    install_virtualenv(True)
    
@runs_once
def update_requirements():
    with cd(env.version_path):
        if 'commit' in run('git log %(previous_revision)s..%(actual_revision)s -- requirements.txt'%env) and env.previous_revision is not None:
            run("""cd %(app_root)s
                   %(virtualenv)s/bin/pip install -r requirements.txt
            """ % env )

def do_backup():
    run("""mysqldump --quick --single-transaction --extended-insert --complete-insert --create-options --add-locks -u %(db_user)s --password="%(db_password)s" %(db_name)s -r %(app_root)s/backups/%(version_dir)s_backup.sql
        gzip -f %(app_root)s/backups/%(version_dir)s_backup.sql
        """ % env)

def do_test(linked=False):
    env.test_app_root = env.app_path if linked else env.version_path
    run("""cd %(test_app_root)s
        echo "drop database if exists %(test_db_name)s" | mysql -u %(dbcreate_user)s  --password="%(dbcreate_password)s"
        %(virtualenv)s/bin/python manage.py test coupon --settings=settings_%(env)s || exit 0;
        """ % env)

def test_migration():
    run("""cd %(version_path)s
        %(virtualenv)s/bin/python manage.py migrate --db-dry-run --no-initial-data --settings=settings_%(env)s
    """ % env)

def do_migration():
    run("""cd %(version_path)s
        %(virtualenv)s/bin/python manage.py syncdb --noinput --settings=settings_%(env)s
        %(virtualenv)s/bin/python manage.py migrate --no-initial-data --settings=settings_%(env)s
    """ % env)

def compile_messages():
    if env.use_locales:
        run("""cd %(version_path)s
            %(virtualenv)s/bin/python manage.py compilemessages
            """ % env)

def link_to_current(setup=False):
    if not setup:
    	# To avoid anyone debugging or accessing an old version of the app, we're removing read access.
        run("""
            chmod 000 `readlink -f %(app_path)s`
        """ % env)

    run("""
        ln -sfn %(version_path)s %(app_path)s
        ln -sfn %(virtualenv)s/lib/python2.6/site-packages/tinymce/media/tiny_mce/ %(version_path)s/media/js/tiny_mce
        ln -sfn %(virtualenv)s/lib/python2.6/site-packages/django/contrib/admin/media/ %(version_path)s/media/admin
        ln -sfn %(app_root)s/upload %(version_path)s/media/upload
        ln -sfn ~/django-form-designer/form_designer/ %(version_path)s/form_designer 
        ln -sfn %(app_root)s/pyenv/src/django-cms/cms/media/cms/ %(version_path)s/media/cms
        """ % env ) 
   
def status():
    if env.pidfile and env.port:
        run("""
            if ps -P "`cat %(pidfile)s`" >&-; then echo "Server is running:" && ps -P "`cat %(pidfile)s`"; else echo "Server is not running\nLog:" && tail -n30 %(app_root)s/log/runserver_errors.log; fi
        """ % env)
    run("""
        cd %(app_path)s
        echo "Deployed at: `ls -l %(app_path)s | grep -Eo '[0-9_]{8,}'`"
        echo "Last commits: \n`tail -n5 %(app_root)s/CHANGELOG`"
        echo "Repo status:"
        git status
    """ % env)



def server_start():
    init()
    if env.wsgi_script:
        run("""ln -sfn %(version_path)s/%(wsgi_script)s %(version_path)s/live.wsgi""" % env)
    
    if env.pidfile and env.runserver_port:
        if env.env == 'dev': #small fix for dev deployment
            with cd('%(app_path)s'%env):
                run('pwd && git reset HEAD --hard') 
        run("""
        cd %(app_path)s
        pkill -P "`cat %(pidfile)s`"
        sleep 5
        nohup %(virtualenv)s/bin/python manage.py runserver localhost:%(runserver_port)s --settings=settings_%(env)s > %(app_root)s/log/runserver.log 2> %(app_root)s/log/runserver_errors.log &
        echo $! | tee %(pidfile)s
        sleep 1
        """ % env)


def send_report():
    import smtplib
    
    message = env.message
    message += """
Current server time: %(timestamp)s
Environment: %(env)s
Branch: %(branch)s
Application path: %(version_path)s
Desired revision: %(revision)s
Current revision: %(actual_revision)s
Changelog:

%(changelog)s
""" % env

    session = smtplib.SMTP(env.settings.EMAIL_HOST)

    if env.settings.EMAIL_HOST_USER:
        session.login(env.settings.EMAIL_HOST_USER, env.settings.EMAIL_HOST_PASSWORD)

    message = """\
From: %s
To: %s
Subject: %s

%s
    """ % (env.from_email, ", ".join(env.recipients), 'Deployment to %s' % env.env, message)

    result = session.sendmail(env.from_email, env.recipients, message)

    if result:
        errstr = ""
        for recip in smtpresult.keys():
            errstr = """Could not delivery mail to: %s""" % (recip)
            raise smtplib.SMTPException, errstr
    print "Sending message to admins"
    

def check_no_local_changes():
    # This checks for uncomitted changes. However if there are any commits that have not been pushed, we are overwriting them. (TODO)
    run("""
        cd %(app_path)s
        if [ "0" -ne "`git status -uno -s | wc -m`" ] ; then echo "Server has unsaved changes:" && git status -uno -s && exit -1 ; fi
    """ % env)
    run("""
        if [ "0" -ne "`git status -uno -s | wc -m`" ] ; then exit -1 ; fi 
    """ % env)

def deploy():
    init()
    
    if not env.force:
        check_no_local_changes()
        
    repo_checkout()
    update_requirements()
    test_migration() 
    compile_messages()
    if env.backup:
        do_backup()
    do_migration()
    link_to_current()
    server_start()
    if env.send_email:
        send_report() 


def backup():
    init()
    do_backup()
    
def test():
    init()
    do_test(linked=True)

