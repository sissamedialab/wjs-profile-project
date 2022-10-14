# WJS - JCOM profile

**Experimental**

A django app for Janeway that enriches the `Account` profile with the
field "profession".

**Important**: needs JCOM graphical theme (because of some modified templates)

The branches `as-plugin` and `simpler-name` have this app in the form
of a Janeway's plugin, but I've abandoned them because of difficulties
in test "finding" (ala `manage.py test plugins.myplugin`) and because
I don't need to be able to enable/disable the plugin TTW.


## Install & use

This is a django app that should live inside Janeway. To use it, proceed as follows:

1. Activate your Janeway's virtual environment and install in development mode:
   `pip install -e .../wjs-profile-project`

2. Migrations should be run specifying the sub-package name:
   `./manage.py migrate jcom_profile`
3. From `janeway/src`, run the following command:

   ```
   python manage.py run_customizations
   ```
   It will add all our customization to Janeway project.

### Available customization commands
| command                                   | arguments | description                                                      |
|-------------------------------------------|-----------|------------------------------------------------------------------|
| `add_coauthors_submission_email_settings` | -         | Add email settings to notify coauthors after article submission. |
| `run_customizations`                      | -         | Run all customization commands to Janeway.                       |

### TODO: (aka "not yet implemented"...)

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


See https://gitlab.sissamedialab.it/medialab/janeway/-/issues/7
