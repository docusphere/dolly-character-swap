import json

import pytest
from PIL import Image

from character_swap.faces import (
    ACTIVE,
    FAILED,
    GONE,
    PROCESSING,
    FaceError,
    FaceLibrary,
    check_photo,
)
from fakes import FakePiAPI


def photo(path, size=(828, 1086), mode="RGB", fmt="JPEG"):
    Image.new(mode, size, (200, 150, 120)).save(path, fmt)
    return path


def library(tmp_path, **kw):
    return FaceLibrary(tmp_path, sleep=lambda _s: None, **kw)


def test_check_photo_saves_an_upright_jpeg(tmp_path):
    out = check_photo(photo(tmp_path / "me.png", fmt="PNG", mode="RGBA"), tmp_path / "out.jpg")
    with Image.open(out) as image:
        assert image.format == "JPEG" and image.mode == "RGB" and image.size == (828, 1086)


@pytest.mark.parametrize(
    ("size", "words"),
    [((156, 226), "at least 300 px"), ((3000, 600), "too wide or too narrow")],
)
def test_check_photo_rejects_what_piapi_would(tmp_path, size, words):
    with pytest.raises(FaceError, match=words):
        check_photo(photo(tmp_path / "me.jpg", size=size), tmp_path / "out.jpg")


def test_check_photo_scales_down_huge_photos(tmp_path):
    out = check_photo(photo(tmp_path / "big.jpg", size=(4000, 5000)), tmp_path / "out.jpg")
    with Image.open(out) as image:
        assert max(image.size) == 3000


def test_check_photo_explains_unreadable_files(tmp_path):
    heic = tmp_path / "IMG_1.heic"
    heic.write_bytes(b"not an image")
    with pytest.raises(FaceError, match="Export it as JPEG"):
        check_photo(heic, tmp_path / "out.jpg")


def test_add_uploads_saves_the_id_first_and_waits_for_active(tmp_path):
    piapi = FakePiAPI()
    face = library(tmp_path).add("frank", photo(tmp_path / "me.jpg"), piapi)
    assert face.status == ACTIVE and face.asset_id == "asset-1"
    assert [p.name for p in piapi.uploads] == ["frank.jpg"]
    rows = json.loads((tmp_path / "faces" / "faces.json").read_text())
    assert rows[0]["name"] == "frank" and rows[0]["asset_id"] == "asset-1"
    assert (tmp_path / "faces" / "frank.jpg").exists()


def test_add_refuses_a_taken_name_and_bad_names(tmp_path):
    lib = library(tmp_path)
    lib.add("frank", photo(tmp_path / "me.jpg"), FakePiAPI())
    with pytest.raises(FaceError, match="already saved"):
        lib.add("frank", photo(tmp_path / "me2.jpg"), FakePiAPI())
    with pytest.raises(FaceError, match="lowercase"):
        lib.add("Frank Jr!", photo(tmp_path / "me3.jpg"), FakePiAPI())


def test_add_stops_waiting_and_keeps_processing(tmp_path):
    piapi = FakePiAPI(assets={"asset-1": "Processing"})
    face = library(tmp_path, polls=2).add("frank", photo(tmp_path / "me.jpg"), piapi)
    assert face.status == PROCESSING and len(piapi.asset_checks) == 2


def test_check_refreshes_and_records_a_purged_face(tmp_path):
    piapi = FakePiAPI()
    lib = library(tmp_path)
    lib.add("frank", photo(tmp_path / "me.jpg"), piapi)
    piapi.assets.clear()  # PiAPI purged it after idle days
    assert lib.check("frank", piapi).status == GONE
    assert lib.get("frank").status == GONE


def test_require_active_explains_each_state(tmp_path):
    piapi = FakePiAPI()
    lib = library(tmp_path)
    lib.add("frank", photo(tmp_path / "me.jpg"), piapi)
    assert lib.require_active("frank", piapi).status == ACTIVE
    piapi.assets["asset-1"] = "Failed"
    with pytest.raises(FaceError, match="rejected"):
        lib.require_active("frank", piapi)
    piapi.assets.clear()
    with pytest.raises(FaceError, match="charswap face renew frank"):
        lib.require_active("frank", piapi)


def test_renew_reuploads_the_same_saved_photo(tmp_path):
    piapi = FakePiAPI()
    lib = library(tmp_path)
    lib.add("frank", photo(tmp_path / "me.jpg"), piapi)
    piapi.assets.clear()
    renewed = lib.renew("frank", piapi)
    assert renewed.status == ACTIVE and renewed.asset_id != "" and len(lib.all()) == 1
    assert [p.name for p in piapi.uploads] == ["frank.jpg", "frank.jpg"]


def test_unknown_and_damaged_library(tmp_path):
    lib = library(tmp_path)
    assert lib.all() == []
    with pytest.raises(FaceError, match="charswap face add"):
        lib.get("nobody")
    (tmp_path / "faces").mkdir()
    (tmp_path / "faces" / "faces.json").write_text("{nope")
    with pytest.raises(FaceError, match="damaged"):
        lib.all()


def test_failed_status_keeps_piapis_reason(tmp_path):
    class Rejecting(FakePiAPI):
        def get_asset(self, asset_id):
            return {"asset_id": asset_id, "status": "Failed", "error": {"message": "no face found"}}

    face = library(tmp_path).add("frank", photo(tmp_path / "me.jpg"), Rejecting())
    assert face.status == FAILED and face.error == "no face found"


def test_renew_does_not_upload_when_the_face_is_still_there(tmp_path):
    piapi = FakePiAPI()
    lib = library(tmp_path)
    lib.add("frank", photo(tmp_path / "me.jpg"), piapi)
    assert lib.renew("frank", piapi).status == ACTIVE
    assert len(piapi.uploads) == 1


def test_renew_explains_a_rejected_face(tmp_path):
    piapi = FakePiAPI()
    lib = library(tmp_path)
    lib.add("frank", photo(tmp_path / "me.jpg"), piapi)
    piapi.assets["asset-1"] = "Failed"
    with pytest.raises(FaceError, match="clearer photo under a new name"):
        lib.renew("frank", piapi)
    assert len(piapi.uploads) == 1


def test_transparency_is_flattened_onto_white(tmp_path):
    src = tmp_path / "t.png"
    Image.new("RGBA", (400, 400), (0, 0, 0, 0)).save(src)
    out = check_photo(src, tmp_path / "out.jpg")
    with Image.open(out) as image:
        assert min(image.getpixel((10, 10))) > 250


def test_a_brief_404_right_after_creating_is_not_gone(tmp_path):
    class Late(FakePiAPI):
        def get_asset(self, asset_id):
            self.asset_checks.append(asset_id)
            return (
                None if len(self.asset_checks) == 1 else {"asset_id": asset_id, "status": "Active"}
            )

    assert library(tmp_path).add("frank", photo(tmp_path / "me.jpg"), Late()).status == ACTIVE


def test_validate_new_name_is_public(tmp_path):
    lib = library(tmp_path)
    lib.validate_new_name("frank")
    with pytest.raises(FaceError, match="lowercase"):
        lib.validate_new_name("Bad Name")


def test_decompression_bomb_gets_the_export_advice(tmp_path, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    with pytest.raises(FaceError, match="Export it as JPEG"):
        check_photo(photo(tmp_path / "big.jpg", size=(400, 400)), tmp_path / "out.jpg")


def test_unknown_keys_in_a_row_are_ignored(tmp_path):
    lib = library(tmp_path)
    lib.add("frank", photo(tmp_path / "me.jpg"), FakePiAPI())
    rows = json.loads(lib.index.read_text())
    rows[0]["future"] = 1
    lib.index.write_text(json.dumps(rows))
    assert lib.get("frank").name == "frank"
