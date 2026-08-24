xcube service (monkeypatches + operational scripts)
=====================================================

Modules layered around the third-party ``xcube`` tile-server library.
``xcube`` itself is treated as an external dependency (mocked at build
time via ``autodoc_mock_imports``, not installed into this docs
environment) since every reference to it in these files is a lazy,
function-body-level import applied at process startup rather than a
module-level dependency.

serve_wrapper
-------------

.. automodule:: serve_wrapper
   :members:
   :undoc-members:
   :show-inheritance:

dataset_cache_patch
--------------------

.. automodule:: dataset_cache_patch
   :members:
   :undoc-members:
   :show-inheritance:

async_datasets_patch
----------------------

.. automodule:: async_datasets_patch
   :members:
   :undoc-members:
   :show-inheritance:

augment_coords
---------------

.. automodule:: augment_coords
   :members:
   :undoc-members:
   :show-inheritance:

prewarm_cache
--------------

.. automodule:: prewarm_cache
   :members:
   :undoc-members:
   :show-inheritance:
