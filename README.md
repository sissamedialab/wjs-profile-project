# WJS - JCOM profile

Add the field "profession" to the user account.

See https://gitlab.sissamedialab.it/medialab/janeway/-/issues/7


## Install & use

TODO: verify that it works as plugin!
TODO: review this file!

This is a django app that should live inside Janeway. To use it, proceed as follows:

1. Activate your Janeway's virtual environment and install in development mode:
   `pip install -e .../wjs-profession'

### Not yet implemented...

1. Activate your Janeway's virtual environment and install the package
   of this app (please see
   https://gitlab.sissamedialab.it/ml-foss/omlpi/-/packages)

2. Add "wjs\_profession" to Janeway's INSTALLED\_APPS in
   `src/core/janeway\_global\_setting.py` like this::
   ```
   INSTALLED_APPS = [
       ...
       'wjs_profession',
   ]
   ```
