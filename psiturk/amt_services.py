import os, sys
import subprocess,signal
from threading import Thread, Event
import urllib2
import json
import datetime
from boto.mturk.connection import MTurkConnection, MTurkRequestError
from boto.mturk.question import ExternalQuestion
from boto.mturk.qualification import LocaleRequirement, \
    PercentAssignmentsApprovedRequirement, Qualifications
from flask import jsonify
import socket
import webbrowser

class MTurkServices:
    def __init__(self, config):
        self.config = config

    def get_active_hits(self):
        self.connect_to_turk()
        # hits = self.mtc.search_hits()
        try:
            hits = self.mtc.get_all_hits()
        except MTurkRequestError:
            return(False)
        active_hits = [hit for hit in hits if not(hit.expired)]
        hits_data = [{'hitid': hit.HITId,
                      'title': hit.Title,
                      'status': hit.HITStatus,
                      'max_assignments': hit.MaxAssignments,
                      'number_assignments_completed': hit.NumberOfAssignmentsCompleted,
                      'number_assignments_pending': hit.NumberOfAssignmentsCompleted,
                      'number_assignments_available': hit.NumberOfAssignmentsAvailable,
                      'creation_time': hit.CreationTime,
                      'expiration': hit.Expiration,
                      } for hit in active_hits]
        return(hits_data)

    def get_workers(self):
        self.connect_to_turk()
        try:
            hits = self.mtc.search_hits(sort_direction='Descending', page_size=20)
            hit_ids = [hit.HITId for hit in hits]
            workers_nested = [self.mtc.get_assignments(
                                hit_id,
                                status="Submitted",
                                sort_by='SubmitTime',
                                page_size=100
                              ) for hit_id in hit_ids]

            workers = [val for subl in workers_nested for val in subl]  # Flatten nested lists
        except MTurkRequestError:
            return(False)
        completed_workers = [worker for worker in workers if worker.AssignmentStatus == "Submitted"]
        worker_data = [{'hitId': worker.HITId,
                        'assignmentId': worker.AssignmentId,
                        'workerId': worker.WorkerId,
                        'submit_time': worker.SubmitTime,
                        'accept_time': worker.AcceptTime
                       } for worker in completed_workers]
        return(worker_data)

    def approve_worker(self, assignment_id):
        self.connect_to_turk()
        try:
            self.mtc.approve_assignment(assignment_id, feedback=None)
        except MTurkRequestError:
            return(False)

    def reject_worker(self, assignment_id):
        self.connect_to_turk()
        try:
            self.mtc.reject_assignment(assignment_id, feedback=None)
        except MTurkRequestError:
            return(False)

    def verify_aws_login(self, key_id, secret_key):
        is_sandbox = self.config.getboolean('HIT Configuration', 'using_sandbox')
        if is_sandbox:
            host = 'mechanicalturk.sandbox.amazonaws.com'
        else:
            host = 'mechanicalturk.amazonaws.com'
        mturkparams = dict(
            aws_access_key_id=key_id,
            aws_secret_access_key=secret_key,
            host=host)
        self.mtc = MTurkConnection(**mturkparams)
        try:
            self.mtc.get_account_balance()
        except MTurkRequestError as e:
            print(e.error_message)
            print('AWS Credentials invalid')
            return 0
        else:
            print('AWS Credentials valid')
            return 1

    def connect_to_turk(self):
        is_sandbox = self.config.getboolean('HIT Configuration', 'using_sandbox')
        if is_sandbox:
            host = 'mechanicalturk.sandbox.amazonaws.com'
        else:
            host = 'mechanicalturk.amazonaws.com'
        
        mturkparams = dict(
            aws_access_key_id = self.config.get('AWS Access', 'aws_access_key_id'),
            aws_secret_access_key = self.config.get('AWS Access', 'aws_secret_access_key'),
            host=host)
        self.mtc = MTurkConnection(**mturkparams)
        
    def register_ad(self):
        # register with the ad server (psiturk.org/ad/register?server=&port=&support_ie=)
        server = self.config.get('Server Parameters', 'host')
        port = self.config.get('Server Parameters', 'port')
        support_ie = self.config.get('HIT Configuration', 'support_ie')
        ad_server_register_url = 'https://psiturk.org/ad/register?server=' + server + '&port=' + port + '&support_ie=' + support_ie
        #ad_server_register_url = 'http://localhost:5004/ad/register?server=' + server + '&port=' + port + '&support_ie=' + support_ie
        response = urllib2.urlopen(ad_server_register_url)
        # 2. get id in response
        data = json.load(response) 
        if data['id'] == "correct parameters not provided":
            print "Error: registering Ad with server, you didn't provide all the required parameters (server, port, support_ie)"
            return False
        elif data['id'] == "localhost not allowed":
            print "Error: attempting to localhost or 127.0.0.1 as your server location to the Ad server, but this is not allowed.  Check the 'host' parameter in config.txt and make it a publically accessible hostname/ip."
            return False
        return data['id']

    def configure_hit(self, ad_id):

        # 3. configure question_url based on the id
        experimentPortalURL = 'https://psiturk.org/ad/' + str(ad_id) 
        #experimentPortalURL = 'http://localhost:5004/ad/' + str(ad_id)
        print experimentPortalURL
        frameheight = 600
        mturkQuestion = ExternalQuestion(experimentPortalURL, frameheight)

        # Qualification:
        quals = Qualifications()
        approve_requirement = self.config.get('HIT Configuration', 'Approve_Requirement')
        quals.add(
            PercentAssignmentsApprovedRequirement("GreaterThanOrEqualTo",
                                                  approve_requirement))
        if self.config.getboolean('HIT Configuration', 'US_only'):
            quals.add(LocaleRequirement("EqualTo", "US"))

        # Specify all the HIT parameters
        self.paramdict = dict(
            hit_type = None,
            question = mturkQuestion,
            lifetime = datetime.timedelta(hours=self.config.getfloat('HIT Configuration', 'HIT_lifetime')),
            max_assignments = self.config.getint('HIT Configuration', 'max_assignments'),
            title = self.config.get('HIT Configuration', 'title'),
            description = self.config.get('HIT Configuration', 'description'),
            keywords = self.config.get('HIT Configuration', 'keywords'),
            reward = self.config.getfloat('HIT Configuration', 'reward'),
            duration = datetime.timedelta(hours=self.config.getfloat('HIT Configuration', 'duration')),
            approval_delay = None,
            questions = None,
            qualifications = quals
        )
    
    def is_signed_up(self):
        access_key_id = self.config.get('AWS Access', 'aws_access_key_id')
        access_key = self.config.get('AWS Access', 'aws_secret_access_key')
        return (access_key_id != 'YourAccessKeyId') and \
               (access_key != 'YourSecreteAccessKey')

    def check_balance(self):
        if self.is_signed_up():
            self.connect_to_turk()
            return(self.mtc.get_account_balance()[0])
        else:
            return('-')

    # TODO (if valid AWS credentials haven't been provided then connect_to_turk() will
    # fail, not error checking here and elsewhere)
    def create_hit(self):
        self.connect_to_turk()
        ad_id=self.register_ad()
        if not ad_id:
            return False
        else:
            self.configure_hit(ad_id)
            myhit = self.mtc.create_hit(**self.paramdict)[0]
            self.hitid = myhit.HITId
            return True
 
    # TODO(Jay): Have a wrapper around functions that serializes them. 
    # Default output should not be serialized.
    def expire_hit(self, hitid):
        self.connect_to_turk()
        self.mtc.expire_hit(hitid)

    def extend_hit(self, hitid, assignments_increment=None, expiration_increment=None):
        self.connect_to_turk()
        self.mtc.extend_hit(hitid, assignments_increment=int(assignments_increment))
        self.mtc.extend_hit(hitid, expiration_increment=int(expiration_increment)*60)

    def get_summary(self):
      try:
          balance = self.check_balance()
          summary = jsonify(balance=str(balance))
          return(summary)
      except MTurkRequestError as e:
          print(e.error_message)
          return(False)
