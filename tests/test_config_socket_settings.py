import copy

import pytest

from mirror.structure import Config
import mirror.config.config


class TestSocketSettingsFromDict:
    def test_full_explicit_values(self):
        s = Config.SocketSettings.from_dict({"uid": 0, "gid": 0, "mode": "0770"})
        assert s.uid == 0
        assert s.gid == 0
        assert s.mode == 0o770

    def test_mode_0o_prefix(self):
        s = Config.SocketSettings.from_dict({"mode": "0o660"})
        assert s.mode == 0o660
        assert s.uid is None
        assert s.gid is None

    def test_empty_dict_defaults(self):
        s = Config.SocketSettings.from_dict({})
        assert s.uid is None
        assert s.gid is None
        assert s.mode == 0o600

    def test_uid_zero_preserved(self):
        # uid=0 must NOT be treated as missing
        s = Config.SocketSettings.from_dict({"uid": 0})
        assert s.uid == 0

    def test_gid_zero_preserved(self):
        s = Config.SocketSettings.from_dict({"gid": 0})
        assert s.gid == 0


class TestSocketSettingsFromDictInvalid:
    def test_uid_bool_raises(self):
        with pytest.raises(ValueError):
            Config.SocketSettings.from_dict({"uid": True})

    def test_gid_string_raises(self):
        with pytest.raises(ValueError):
            Config.SocketSettings.from_dict({"gid": "1000"})

    def test_uid_float_raises(self):
        with pytest.raises(ValueError):
            Config.SocketSettings.from_dict({"uid": 1.0})

    def test_mode_int_raises(self):
        # mode must be a string, not an int
        with pytest.raises(ValueError):
            Config.SocketSettings.from_dict({"mode": 504})

    def test_mode_invalid_octal_raises(self):
        with pytest.raises(ValueError):
            Config.SocketSettings.from_dict({"mode": "999"})


class TestSocketSettingsToConfigDict:
    def test_mode_is_string(self):
        s = Config.SocketSettings()
        d = s.to_config_dict()
        assert isinstance(d["mode"], str)

    def test_default_round_trip(self):
        s = Config.SocketSettings()
        restored = Config.SocketSettings.from_dict(s.to_config_dict())
        assert (restored.uid, restored.gid, restored.mode) == (s.uid, s.gid, s.mode)

    def test_uid_gid_mode_round_trip(self):
        s = Config.SocketSettings(uid=0, gid=0, mode=0o770)
        d = s.to_config_dict()
        restored = Config.SocketSettings.from_dict(d)
        assert (restored.uid, restored.gid, restored.mode) == (0, 0, 0o770)

    def test_mode_only_round_trip(self):
        s = Config.SocketSettings(uid=None, gid=None, mode=0o600)
        d = s.to_config_dict()
        assert "uid" not in d
        assert "gid" not in d
        restored = Config.SocketSettings.from_dict(d)
        assert (restored.uid, restored.gid, restored.mode) == (None, None, 0o600)


class TestConfigLoadFromDictIntegration:
    def test_default_no_socket_key(self):
        raw = copy.deepcopy(mirror.config.config.DEFAULT_CONFIG)
        conf = Config.load_from_dict(raw)
        assert conf.socket.mode == 0o600
        assert conf.socket.uid is None
        assert conf.socket.gid is None

    def test_with_socket_key(self):
        raw = copy.deepcopy(mirror.config.config.DEFAULT_CONFIG)
        raw["settings"]["socket"] = {"gid": 1000, "mode": "0770"}
        conf = Config.load_from_dict(raw)
        assert conf.socket.gid == 1000
        assert conf.socket.mode == 0o770
        assert conf.socket.uid is None
