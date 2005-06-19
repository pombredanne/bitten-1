import logging
import unittest

from bitten.util import beep
from bitten.util.xmlio import Element


class MockSession(beep.Initiator):

    def __init__(self):
        self.profiles = {}
        self.sent_messages = []
        self.channelno = beep.cycle_through(1, 2147483647, step=2)
        self.channels = {0: beep.Channel(self, 0, beep.ManagementProfileHandler)}
        del self.sent_messages[0] # Clear out the management greeting
        self.channels[0].seqno = [beep.serial(), beep.serial()]

    def send_data_frame(self, cmd, channel, msgno, more, seqno, ansno=None,
                        payload=''):
        self.sent_messages.append((cmd, channel, msgno, more, seqno, ansno,
                                   payload.strip()))


class MockProfileHandler(object):
    URI = 'http://example.com/mock'

    def __init__(self, channel):
        self.handled_messages = []

    def handle_connect(self):
        pass

    def handle_disconnect(self):
        pass

    def handle_msg(self, msgno, message):
        text = message.as_string().strip()
        self.handled_messages.append(('MSG', msgno, text))

    def handle_rpy(self, msgno, message):
        text = message.as_string().strip()
        self.handled_messages.append(('RPY', msgno, text))


class ChannelTestCase(unittest.TestCase):

    def setUp(self):
        self.session = MockSession()

    def test_handle_single_msg_frame(self):
        """
        Verify that the channel correctly passes a single frame MSG to the
        profile.
        """
        channel = beep.Channel(self.session, 0, MockProfileHandler)
        channel.handle_data_frame('MSG', 0, False, 0, None, 'foo bar')
        self.assertEqual(('MSG', 0, 'foo bar'),
                         channel.profile.handled_messages[0])

    def test_handle_segmented_msg_frames(self):
        """
        Verify that the channel combines two segmented messages and passed the
        recombined message to the profile.
        """
        channel = beep.Channel(self.session, 0, MockProfileHandler)
        channel.handle_data_frame('MSG', 0, True, 0, None, 'foo ')
        channel.handle_data_frame('MSG', 0, False, 4, None, 'bar')
        self.assertEqual(('MSG', 0, 'foo bar'),
                         channel.profile.handled_messages[0])

    def test_handle_out_of_sync_frame(self):
        """
        Verify that the channel detects out-of-sync frames and bails.
        """
        channel = beep.Channel(self.session, 0, MockProfileHandler)
        channel.handle_data_frame('MSG', 0, False, 0L, None, 'foo bar')
        # The next sequence number should be 8; send 12 instead
        self.assertRaises(beep.ProtocolError, channel.handle_data_frame, 'MSG',
                          0, False, 12L, None, 'foo baz')

    def test_send_single_frame_message(self):
        """
        Verify that the channel passes a sent message up to the session for
        transmission with the correct parameters. Also assert that the
        corresponding message number (0) is reserved.
        """
        channel = beep.Channel(self.session, 0, MockProfileHandler)
        msgno = channel.send_msg(beep.MIMEMessage('foo bar'))
        self.assertEqual(('MSG', 0, msgno, False, 0L, None, 'foo bar'),
                         self.session.sent_messages[0])
        assert msgno in channel.msgnos

    def test_send_frames_seqno_incrementing(self):
        """
        Verify that the sequence numbers of outgoing frames are incremented as
        expected.
        """
        channel = beep.Channel(self.session, 0, MockProfileHandler)
        channel.send_msg(beep.MIMEMessage('foo bar'))
        channel.send_rpy(0, beep.MIMEMessage('nil'))
        self.assertEqual(('MSG', 0, 0, False, 0L, None, 'foo bar'),
                         self.session.sent_messages[0])
        self.assertEqual(('RPY', 0, 0, False, 8L, None, 'nil'),
                         self.session.sent_messages[1])

    def test_send_message_msgno_incrementing(self):
        """
        Verify that the message number is incremented for subsequent outgoing
        messages.
        """
        channel = beep.Channel(self.session, 0, MockProfileHandler)
        msgno = channel.send_msg(beep.MIMEMessage('foo bar'))
        assert msgno == 0
        self.assertEqual(('MSG', 0, msgno, False, 0L, None, 'foo bar'),
                         self.session.sent_messages[0])
        assert msgno in channel.msgnos
        msgno = channel.send_msg(beep.MIMEMessage('foo baz'))
        assert msgno == 1
        self.assertEqual(('MSG', 0, msgno, False, 8L, None, 'foo baz'),
                         self.session.sent_messages[1])
        assert msgno in channel.msgnos

    def test_send_reply(self):
        """
        Verify that sending an ANS message is processed correctly.
        """
        channel = beep.Channel(self.session, 0, MockProfileHandler)
        channel.send_rpy(0, beep.MIMEMessage('foo bar'))
        self.assertEqual(('RPY', 0, 0, False, 0L, None, 'foo bar'),
                         self.session.sent_messages[0])

    def test_message_and_reply(self):
        """
        Verify that a message number is deallocated after a final reply has been
        received.
        """
        channel = beep.Channel(self.session, 0, MockProfileHandler)
        msgno = channel.send_msg(beep.MIMEMessage('foo bar'))
        self.assertEqual(('MSG', 0, msgno, False, 0L, None, 'foo bar'),
                         self.session.sent_messages[0])
        assert msgno in channel.msgnos
        channel.handle_data_frame('RPY', msgno, False, 0, None, '42')
        self.assertEqual(('RPY', msgno, '42'),
                         channel.profile.handled_messages[0])
        assert msgno not in channel.msgnos

    def test_send_error(self):
        """
        Verify that sending an ERR message is processed correctly.
        """
        channel = beep.Channel(self.session, 0, MockProfileHandler)
        channel.send_err(0, beep.MIMEMessage('oops'))
        self.assertEqual(('ERR', 0, 0, False, 0L, None, 'oops'),
                         self.session.sent_messages[0])

    def test_send_answers(self):
        """
        Verify that sending an ANS message is processed correctly.
        """
        channel = beep.Channel(self.session, 0, MockProfileHandler)
        ansno = channel.send_ans(0, beep.MIMEMessage('foo bar'))
        assert ansno == 0
        self.assertEqual(('ANS', 0, 0, False, 0L, ansno, 'foo bar'),
                         self.session.sent_messages[0])
        assert 0 in channel.ansnos
        ansno = channel.send_ans(0, beep.MIMEMessage('foo baz'))
        assert ansno == 1
        self.assertEqual(('ANS', 0, 0, False, 8L, ansno, 'foo baz'),
                         self.session.sent_messages[1])
        assert 0 in channel.ansnos
        channel.send_nul(0)
        self.assertEqual(('NUL', 0, 0, False, 16L, None, ''),
                         self.session.sent_messages[2])
        assert 0 not in channel.ansnos


class ManagementProfileHandlerTestCase(unittest.TestCase):

    def setUp(self):
        self.session = MockSession()
        self.channel = self.session.channels[0]
        self.profile = self.channel.profile

    def test_send_greeting(self):
        """
        Verify that the management profile sends a greeting reply when
        initialized.
        """
        self.profile.handle_connect()
        self.assertEqual(1, len(self.session.sent_messages))
        xml = Element('greeting')
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.assertEqual(('RPY', 0, 0, False, 0, None, message),
                         self.session.sent_messages[0])

    def test_send_greeting_with_profile(self):
        """
        Verify that the management profile sends a greeting with a list of
        supported profiles reply when initialized.
        """
        self.session.profiles['test'] = MockProfileHandler
        self.profile.handle_connect()
        self.assertEqual(1, len(self.session.sent_messages))
        xml = Element('greeting')[Element('profile', uri='test')]
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.assertEqual(('RPY', 0, 0, False, 0, None, message),
                         self.session.sent_messages[0])

    def test_handle_greeting(self):
        """
        Verify that the management profile calls the greeting_received() method
        of the initiator session.
        """
        def greeting_received(profiles):
            greeting_received.called = True
            self.assertEqual(['test'], profiles)
        greeting_received.called = False
        self.session.greeting_received = greeting_received
        xml = Element('greeting')[Element('profile', uri='test')]
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.channel.handle_data_frame('RPY', 0, False, 0L, None, message)
        assert greeting_received.called

    def test_send_error(self):
        """
        Verify that a negative reply is sent as expected.
        """
        self.profile.send_error(0, 521, 'ouch')
        xml = Element('error', code=521)['ouch']
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.assertEqual(('ERR', 0, 0, False, 0, None, message),
                         self.session.sent_messages[0])

    def test_send_start(self):
        """
        Verify that a <start> request is sent correctly.
        """
        self.profile.send_start([MockProfileHandler])
        xml = Element('start', number="1")[
            Element('profile', uri=MockProfileHandler.URI)
        ]
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.assertEqual(('MSG', 0, 0, False, 0, None, message),
                         self.session.sent_messages[0])

    def test_send_start_ok(self):
        """
        Verify that a positive reply to a <start> request is handled correctly,
        and the channel is created.
        """
        self.profile.send_start([MockProfileHandler])
        xml = Element('profile', uri=MockProfileHandler.URI)
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.channel.handle_data_frame('RPY', 0, False, 0L, None, message)
        assert isinstance(self.session.channels[1].profile, MockProfileHandler)

    def test_send_start_error(self):
        """
        Verify that a negative reply to a <close> request is handled correctly,
        and no channel gets created.
        """
        self.profile.send_start([MockProfileHandler])
        xml = Element('error', code=500)['ouch']
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.channel.handle_data_frame('ERR', 0, False, 0L, None, message)
        assert not 1 in self.session.channels

    def test_send_start_ok_with_callback(self):
        """
        Verify that user-supplied callback for positive replies is invoked
        when a <profile> reply is received in response to a <start> request.
        """
        def handle_ok(channelno, profile_uri):
            self.assertEqual(1, channelno)
            self.assertEqual(MockProfileHandler.URI, profile_uri)
            handle_ok.called = True
        handle_ok.called = False
        def handle_error(code, text):
            handle_error.called = True
        handle_error.called = False
        self.profile.send_start([MockProfileHandler], handle_ok=handle_ok,
                                handle_error=handle_error)

        xml = Element('profile', uri=MockProfileHandler.URI)
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.channel.handle_data_frame('RPY', 0, False, 0L, None, message)
        assert isinstance(self.session.channels[1].profile, MockProfileHandler)
        assert handle_ok.called
        assert not handle_error.called

    def test_send_start_error_with_callback(self):
        """
        Verify that user-supplied callback for negative replies is invoked
        when an error is received in response to a <start> request.
        """
        def handle_ok(channelno, profile_uri):
            handle_ok.called = True
        handle_ok.called = False
        def handle_error(code, text):
            self.assertEqual(500, code)
            self.assertEqual('ouch', text)
            handle_error.called = True
        handle_error.called = False
        self.profile.send_start([MockProfileHandler], handle_ok=handle_ok,
                                handle_error=handle_error)

        xml = Element('error', code=500)['ouch']
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.channel.handle_data_frame('ERR', 0, False, 0L, None, message)
        assert not 1 in self.session.channels
        assert not handle_ok.called
        assert handle_error.called

    def test_send_close(self):
        """
        Verify that a <close> request is sent correctly.
        """
        self.profile.send_close(1, code=200)
        xml = Element('close', number=1, code=200)
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.assertEqual(('MSG', 0, 0, False, 0, None, message),
                         self.session.sent_messages[0])

    def test_send_close_ok(self):
        """
        Verify that a positive reply to a <close> request is handled correctly,
        and the channel is closed.
        """
        self.session.channels[1] = beep.Channel(self.session, 1,
                                                MockProfileHandler)
        self.profile.send_close(1, code=200)

        xml = Element('ok')
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.channel.handle_data_frame('RPY', 0, False, 0L, None, message)
        assert 1 not in self.session.channels

    def test_send_close_error(self):
        """
        Verify that a negative reply to a <close> request is handled correctly,
        and the channel stays open.
        """
        self.session.channels[1] = beep.Channel(self.session, 1,
                                                MockProfileHandler)
        self.profile.send_close(1, code=200)

        xml = Element('error', code=500)['ouch']
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.channel.handle_data_frame('ERR', 0, False, 0L, None, message)
        assert 1 in self.session.channels

    def test_send_close_ok_with_callback(self):
        """
        Verify that user-supplied callback for positive replies is invoked
        when an <ok> reply is received in response to a <close> request.
        """
        self.session.channels[1] = beep.Channel(self.session, 1,
                                                MockProfileHandler)
        def handle_ok():
            handle_ok.called = True
        handle_ok.called = False
        def handle_error(code, text):
            handle_error.called = True
        handle_error.called = False
        self.profile.send_close(1, code=200, handle_ok=handle_ok,
                                handle_error=handle_error)

        xml = Element('profile', uri=MockProfileHandler.URI)
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.channel.handle_data_frame('RPY', 0, False, 0L, None, message)
        assert 1 not in self.session.channels
        assert handle_ok.called
        assert not handle_error.called

    def test_send_close_error_with_callback(self):
        """
        Verify that user-supplied callback for negative replies is invoked
        when an error is received in response to a <close> request.
        """
        self.session.channels[1] = beep.Channel(self.session, 1,
                                                MockProfileHandler)
        def handle_ok(channelno, profile_uri):
            handle_ok.called = True
        handle_ok.called = False
        def handle_error(code, text):
            self.assertEqual(500, code)
            self.assertEqual('ouch', text)
            handle_error.called = True
        handle_error.called = False
        self.profile.send_close(1, code=200, handle_ok=handle_ok,
                                handle_error=handle_error)

        xml = Element('error', code=500)['ouch']
        message = beep.MIMEMessage(xml, beep.BEEP_XML).as_string()
        self.channel.handle_data_frame('ERR', 0, False, 0L, None, message)
        assert 1 in self.session.channels
        assert not handle_ok.called
        assert handle_error.called


def suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(ChannelTestCase, 'test'))
    suite.addTest(unittest.makeSuite(ManagementProfileHandlerTestCase, 'test'))
    return suite

if __name__ == '__main__':
    logging.getLogger().setLevel(logging.CRITICAL)
    unittest.main(defaultTest='suite')
