from tools.qa_resource_snapshot import resource_snapshot, changed_resources


def test_motion_replacement_is_detected_even_when_moc_and_settings_are_unchanged(tmp_path):
    model, web = tmp_path / "model", tmp_path / "web"
    (model / "motions").mkdir(parents=True)
    web.mkdir()
    (model / "Maple.moc3").write_bytes(b"unchanged native model fixture")
    (model / "Maple.model3.json").write_text("{}")
    (model / "motions" / "idle.motion3.json").write_text("old motion")
    (web / "app.js").write_text("renderer")
    before = resource_snapshot(model, web)
    (model / "motions" / "idle.motion3.json").write_text("new motion")
    assert changed_resources(before, resource_snapshot(model, web)) == [
        "assets/live2d/Maple/motions/idle.motion3.json"]


def test_removed_texture_and_added_shader_are_both_reported(tmp_path):
    model, web = tmp_path / "model", tmp_path / "web"
    model.mkdir()
    web.mkdir()
    (model / "Maple.moc3").write_bytes(b"model fixture")
    texture = model / "texture.png"
    texture.write_bytes(b"texture fixture")
    (web / "app.js").write_text("renderer")
    before = resource_snapshot(model, web)
    texture.unlink()
    (web / "shader.frag").write_text("shader")
    assert changed_resources(before, resource_snapshot(model, web)) == [
        "assets/live2d/Maple/texture.png", "web/dist/shader.frag"]
