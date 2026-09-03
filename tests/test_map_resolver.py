"""Map Resolver 测试：SongCore hash 算法 + info.dat 读取。"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.maps.resolver import compute_level_hash, read_level_info  # noqa: E402

LEVELS_DIR = pathlib.Path(
    r"D:\BSManager\BSInstances\1.40.8\Beat Saber_Data\CustomLevels")
SECRET_BOSS_HASH = "807E71EB310B8AEBA98A643C3E8C390E24E89A80"


class TestLevelHash(unittest.TestCase):
    def test_secret_boss_hash(self):
        """真实谱面：算法结果必须等于 Replay 中的 map_hash。"""
        if not LEVELS_DIR.exists():
            self.skipTest("CustomLevels 目录不存在")
        folder = next((p for p in LEVELS_DIR.iterdir()
                       if p.is_dir() and "SECRET BOSS" in p.name), None)
        if folder is None:
            self.skipTest("SECRET BOSS 谱面不在本地")
        h = compute_level_hash(folder)
        self.assertEqual(h, SECRET_BOSS_HASH)

    def test_synthetic_level(self):
        """合成谱面目录：验证 hash = SHA1(info.dat + beatmaps 顺序拼接)。"""
        import hashlib
        import json
        tmp = PROJECT_ROOT / "_tmp" / "test_level"
        tmp.mkdir(parents=True, exist_ok=True)
        info = {
            "_version": "2.1.0", "_songName": "TestSong",
            "_songAuthorName": "Author", "_levelAuthorName": "Mapper",
            "_beatsPerMinute": 120,
            "_difficultyBeatmapSets": [{
                "_beatmapCharacteristicName": "Standard",
                "_difficultyBeatmaps": [
                    {"_difficulty": "Expert", "_beatmapFilename": "ExpertStandard.dat"},
                ],
            }],
        }
        (tmp / "Info.dat").write_text(json.dumps(info), encoding="utf-8")
        (tmp / "ExpertStandard.dat").write_text('{"notes":[]}', encoding="utf-8")
        # 干扰文件：不应参与 hash
        (tmp / "cover.jpg").write_bytes(b"\xff\xd8\xff")

        h = compute_level_hash(tmp)
        expect = hashlib.sha1(
            (tmp / "Info.dat").read_bytes() +
            (tmp / "ExpertStandard.dat").read_bytes()
        ).hexdigest().upper()
        self.assertEqual(h, expect)

        meta = read_level_info(tmp)
        self.assertEqual(meta["song_name"], "TestSong")
        self.assertEqual(meta["mapper"], "Mapper")
        self.assertEqual(len(meta["difficulties"]), 1)
        self.assertEqual(meta["difficulties"][0]["difficulty"], "Expert")


class TestSongCoreCache(unittest.TestCase):
    def test_cache_load(self):
        from backend.db.repository import Repository
        from backend.maps.resolver import MapResolver
        cache = pathlib.Path(
            r"D:\BSManager\BSInstances\1.40.8\UserData\SongCore\SongHashData.dat")
        if not cache.exists():
            self.skipTest("SongCore 缓存不存在")
        db = PROJECT_ROOT / "_tmp" / "saberlab_test.sqlite"
        repo = Repository(db)
        resolver = MapResolver(str(LEVELS_DIR), repo, str(cache))
        data = resolver.load_songcore_cache()
        self.assertGreater(len(data), 100)
        self.assertIn(SECRET_BOSS_HASH, set(data.values()))


if __name__ == "__main__":
    unittest.main()
