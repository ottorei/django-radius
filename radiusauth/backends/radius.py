import logging
from StringIO import StringIO

from pyrad.packet import AccessRequest, AccessAccept, AccessReject
from pyrad.client import Client, Timeout
from pyrad.dictionary import Dictionary

from django.conf import settings
from django.contrib.auth.models import User

DICTIONARY = u"""
ATTRIBUTE User-Name     1 string
ATTRIBUTE User-Password 2 string encrypt=1
"""

REALM_SEPARATOR = '@'

def utf8_encode_args(f):
    """Decorator to encode string arguments as UTF-8"""
    def encoded(*args):
        nargs = [ arg.encode('utf-8') for arg in args ]
        return f(*nargs)
    return encoded

class RADIUSBackend(object):
    """
    Standard RADIUS authentication backend for Django. Uses the server details
    specified in settings.py (RADIUS_SERVER, RADIUS_PORT and RADIUS_SECRET).
    """
    supports_anonymous_user = False
    supports_object_permissions = False

    def _get_dictionary(self):
        """
        Get the pyrad Dictionary object which will contain our RADIUS user's
        attributes. Fakes a file-like object using StringIO.
        """
        return Dictionary(StringIO(DICTIONARY))

    def _get_auth_packet(self, username, password, client):
        """
        Get the pyrad authentication packet for the username/password and the
        given pyrad client.
        """
        pkt = client.CreateAuthPacket(code=AccessRequest,
                                      User_Name=username)
        pkt["User-Password"] = pkt.PwCrypt(password)
        return pkt

    def _get_client(self, server):
        """
        Get the pyrad client for a given server. RADIUS server is described by
        a 3-tuple: (<hostname>, <port>, <secret>).
        """
        return Client(server=server[0],
                      authport=server[1],
                      secret=server[2],
                      dict=self._get_dictionary(),
                     )

    def _get_server_from_settings(self):
        """
        Get the RADIUS server details from the settings file.
        """
        return (
            settings.RADIUS_SERVER,
            settings.RADIUS_PORT,
            settings.RADIUS_SECRET
        )

    def _perform_radius_auth(self, client, packet):
        """
        Perform the actual radius authentication by passing the given packet
        to the server which `client` is bound to.
        Returns True or False depending on whether the user is authenticated
        successfully.
        """
        try:
            reply = client.SendPacket(packet)
        except Timeout, e:
            logging.error("RADIUS timeout occurred contacting %s:%s" % \
                          (client.server, client.authport)
                         )
            return False
        except Exception, e:
            logging.error("RADIUS error: %s" % e)
            return False

        if reply.code == AccessReject:
            logging.warning("RADIUS access rejected for user '%s'" % \
                            packet['User-Name'])
            return False
        elif reply.code != AccessAccept:
            logging.error("RADIUS access error for user '%s' (code %s)" % \
                          (packet['User-Name'], reply.code)
                         )
            return False

        logging.info("RADIUS access granted for user '%s'" % \
                     packet['User-Name'])
        return True

    def _radius_auth(self, server, username, password):
        """
        Authenticate the given username/password against the RADIUS server
        described by `server`.
        """
        client = self._get_client(server)
        packet = self._get_auth_packet(username, password, client)
        return self._perform_radius_auth(client, packet)

    def get_django_user(self, username, password=None):
        """
        Get the Django user with the given username, or create one if it
        doesn't already exist. If `password` is given, then set the user's
        password to that (regardless of whether the user was created or not).
        """
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            user = User(username=username)

        if password is not None:
            user.set_password(password)
            user.save()

        return user

    @utf8_encode_args
    def authenticate(self, username, password):
        """
        Check credentials against RADIUS server and return a User object or
        None.
        """
        server = self.get_server_from_settings(None)
        result = self._radius_auth(server, username, password)

        if result:
            return self.get_django_user(username, password)

        return None

    def get_user(self, user_id):
        """
        Get the user with the ID of `user_id`. Authentication backends must
        implement this method.
        """
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

class RADIUSRealmBackend(object):
    """
    Advanced realm-based RADIUS backend. Authenticates users with a username in
    the format <username>@<realm>, otherwise ignores the request so it can be
    handled by another backend.

    The server to authenticate with is defined by the result of calling
    get_server(realm), where `realm` is the portion of the username after the
    '@' sign. For example, `user@example.com` has a realm of `example.com`.

    By default, this class always uses the RADIUS server specified in the
    settings file. Subclasses should override the `get_server` method to
    provide their own logic. Return a 3-tuple (<hostname>, <port>, <secret>).
    """
    def _parse_username(self, username):
        """
        Get the server details to use for a given username, assumed to be in
        the format <username@realm>.
        """
        user, _, realm = username.rpartition(REALM_SEPARATOR)
        if not user:
            return (username, None)
        return (user, realm)

    def get_server(self, realm):
        return self._get_server_from_settings()

    @utf8_encode_args
    def authenticate(self, username, password):
        """
        Check credentials against RADIUS server and return a User object or
        None. Username is expected to be of the format <username>@<realm>. If
        it isn't, return None.
        """
        user, realm = self._parse_username(username)

        if not realm:
            return None

        server = self.get_server(realm)
        result = self._radius_auth(server, user, password)

        if result:
            return self.get_django_user(username, password)

        return None
