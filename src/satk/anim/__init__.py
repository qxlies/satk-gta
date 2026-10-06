"""IFP animations (``satk anim ...``): list, extract to JSON, write, merge, check against a skeleton, Blender.

Modules:

* :mod:`.ifp` - full keyframe codec for ANP3 (SA), ANP2 and ANPK (III/VC style, ``cuts.img``): read every
  key frame (compressed int16 or float, as stored), write them back bit-exact; :func:`satk.formats.ifp.parse_ifp`
  validates the structure first;
* :mod:`.jsonio` - the editable JSON form ``satk-anim/1`` (seconds, quaternions, metres);
* :mod:`.skeleton` - skeletons of skinned DFFs (HAnim bone ids) and the built-in SA ped skeleton
  (``data/anim/ped_skeleton.json``, derived from the vanilla peds), the engine's bone-name table;
* :mod:`.check` - bone ids/names against a skeleton, root motion, key order, compression ranges;
* :mod:`.mta` - an MTA:SA client resource (``engineLoadIFP`` + ``engineReplaceAnimation``) for an IFP;
* :mod:`.source` - what a target names (file, ``<img>/<entry>``, ``ifp:``/``anim:`` SIDs, a profile's IFPs);
* :mod:`.blender` - headless Blender jobs (GPL side in ``blender/satk_blender/anim``): apply an IFP animation to
  a ped imported by DragonFF, export actions back to IFP key frames.

Stdlib only at import time; Blender is started only by the ``to-blender``/``from-blender`` operations.
"""
