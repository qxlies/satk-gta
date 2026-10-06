"""satk.look: SA-like previews of models (vanilla, the agent's own files, live Blender sessions).

* :mod:`satk.look.gamelook` (stdlib only, also imported inside Blender): the numbers of the game look:
  the dirt formula, lamp and paint keys, the timecyc light of a game time (ambient, object ambient,
  direction, colour filter), the shared preview light and the display gamma, with the engine source of
  each rule. The soft renderer (``satk.model3d.softrender``) and the Blender looks use the same numbers.
* :mod:`satk.look.inputs`: SIDs, DFF paths, mod folders and ``session:NAME`` -> preview entries.
* :mod:`satk.look.lineup`: class peers of a model for a same-scale lineup (index SQL).
* :mod:`satk.look.compose`: the JPEG sheet of the rendered cells and the lossless texture crops.
* :mod:`satk.look.ops`: the operation ``blender.preview``.

The Blender side lives in ``blender/satk_blender/look`` (GPL): ``api.apply`` (looks ``game|clay|raw|wire``),
``api.render_views`` and ``api.sheet`` (contract K5), the preview job and the studio methods ``look.*``.
"""
