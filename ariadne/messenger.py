#!/usr/bin/env python3

# Part of Ariadne Shell Handler (a fork of Penelope, GPL-3.0-or-later). See LICENSE.

# Split out of engine.py into its own module: Messenger is self-contained
# (stdlib only) but options.py needs Messenger.LEN_BYTES/TYPE_BYTES/
# STREAM_BYTES to validate --network-buffer-size, and options.py is created
# well before engine.py (which depends on options) in the import order, so
# Messenger can't stay in engine.py without creating a cycle.

from .compat import *

class Messenger:
	SHELL = 1
	RESIZE = 2
	EXEC = 3
	STREAM = 4

	STREAM_CODE = '!H'
	STREAM_BYTES = struct.calcsize(STREAM_CODE)

	LEN_CODE = 'H'
	_LEN_CODE = '!' + LEN_CODE
	LEN_BYTES = struct.calcsize(LEN_CODE)

	TYPE_CODE = 'B'
	_TYPE_CODE = '!' + TYPE_CODE
	TYPE_BYTES = struct.calcsize(TYPE_CODE)

	HEADER_CODE = '!' + LEN_CODE + TYPE_CODE
	MAX_PAYLOAD = (1 << (8 * LEN_BYTES)) - 1 - TYPE_BYTES

	def __init__(self, bufferclass):
		self.len = None
		self.input_buffer = bufferclass()
		self.length_buffer = bufferclass()
		self.message_buffer = bufferclass()

	def message(_type, _data):
		if len(_data) > Messenger.MAX_PAYLOAD:
			raise ValueError("Messenger frame payload too large: %d > %d bytes" % (len(_data), Messenger.MAX_PAYLOAD))
		return struct.pack(Messenger.HEADER_CODE, len(_data) + Messenger.TYPE_BYTES, _type) + _data
	message = staticmethod(message)

	def feed(self, data):
		self.input_buffer.write(data)
		self.input_buffer.seek(0)

		while True:
			if not self.len:
				len_need = Messenger.LEN_BYTES - self.length_buffer.tell()
				data = self.input_buffer.read(len_need)
				self.length_buffer.write(data)
				if len(data) != len_need:
					break

				self.len = struct.unpack(Messenger._LEN_CODE, self.length_buffer.getvalue())[0]
				self.length_buffer.seek(0)
				self.length_buffer.truncate()
			else:
				data_need = self.len - self.message_buffer.tell()
				data = self.input_buffer.read(data_need)
				self.message_buffer.write(data)
				if len(data) != data_need:
					break

				self.message_buffer.seek(0)
				_type = struct.unpack(Messenger._TYPE_CODE, self.message_buffer.read(Messenger.TYPE_BYTES))[0]
				_message = self.message_buffer.read()

				self.len = None
				self.message_buffer.seek(0)
				self.message_buffer.truncate()
				yield _type, _message

		self.input_buffer.seek(0)
		self.input_buffer.truncate()


