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
3. Add the following to your local custom janeway settings:
   ```python
   WJS_ARTICLE_ASSIGNMENT_FUNCTIONS = {
       None: "wjs.jcom_profile.events.assignment.default_assign_editors_to_articles",
     }

   ```

4. From `janeway/src`, run the following command:

   ```
   python manage.py run_customizations
   ```
   It will add all our customization to Janeway project.

Please see [this wiki](https://pre-commit.com/) for a detailed list of installation steps.

### Compile frontend assets

To compile frontend assets in the JCOM-Theme theme::

   `./build-assets.sh`

In order for this to work you have to install `sudo apt install inotify-tools`.

### Installing plugins

Installin plugins is handled by `link_plugins` management command which links plugins in janeway directory (if not present already) and run the plugin installation process if not linked yet.

### Available customization commands

| command                                          | arguments | description                                                      |
|--------------------------------------------------|-----------|------------------------------------------------------------------|
| `add_user_as_main_author_setting`                | -         | Add `user_automatically_main_author` setting.                    |
| `add_coauthors_submission_email_settings`        | -         | Add email settings to notify coauthors after article submission. |
| `add_custom_subscribe_email_message_settings.py` | -         | Add email message body for anonymous newsletter subscriptions.   |
| `link_plugins`                                   | -         | Link and install janeway plugins.                                |
| `run_customizations`                             | -         | Run all customization commands to Janeway.                       |

### pre-commit

This project uses [pre-commit](https://pre-commit.com/) hooks to enforce code style and linting.

When you make a commit, it will trigger `pre-commit` hooks which will check staged files style
on `.pre-commit-config.yaml` rules basis.

1. Install `pre-commit`:
   ```shell
   pip install pre-commit
   ```

2. Install `pre-commit` hooks script in repository root:
   ```shell
   pre-commit install
   ```

3. If you want to update `pre-commit` dependencies, run the following command:
   ```shell
   pre-commit autoupdate
   ```

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
