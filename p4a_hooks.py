"""python-for-android hooks for SH Download Manager.

Buildozer passes ``p4a.hook`` to every p4a command.

``after_apk_build`` runs right after p4a renders AndroidManifest.xml and before
Gradle builds it.  The androidx ``FileProvider`` used by "Open" / "Open Folder"
has to be a child of ``<application>``, and p4a has no flag for providers:
``--extra-manifest-application-arguments`` inserts text into the opening
``<application ...>`` tag (as attributes), which produces invalid XML.  So the
provider is injected here instead.
"""

import os

FILE_PROVIDER = """
        <provider
            android:name="androidx.core.content.FileProvider"
            android:authorities="org.shdownloadmanager.shdownloadmanager.fileprovider"
            android:exported="false"
            android:grantUriPermissions="true">
            <meta-data
                android:name="android.support.FILE_PROVIDER_PATHS"
                android:resource="@xml/file_paths" />
        </provider>
"""


def after_apk_build(toolchain):
    """Add the FileProvider to the generated manifest."""
    try:
        dist_dir = toolchain._dist.dist_dir
    except Exception:
        return

    manifest = os.path.join(dist_dir, "src", "main", "AndroidManifest.xml")
    if not os.path.exists(manifest):
        return

    with open(manifest, "r", encoding="utf-8") as handle:
        text = handle.read()

    if "androidx.core.content.FileProvider" in text:
        return

    text = text.replace("</application>", FILE_PROVIDER + "    </application>")
    with open(manifest, "w", encoding="utf-8") as handle:
        handle.write(text)
