"""satk.viewscene: the agent's own models, vehicles and peds in the running viewer.

``satk view place|vehicle|ped|reload|remove|list`` drive the SAAP ``scene.*`` methods of a
viewer target (``proto/SAAP-v1.md`` §11): Ariane draws the placed entities in the live map
(colour, ID and depth layers, pick), the mock endpoint emulates them as boxes. Nothing here
writes game files; the endpoint only reads the DFF/TXD/IFP files it is given.
"""
