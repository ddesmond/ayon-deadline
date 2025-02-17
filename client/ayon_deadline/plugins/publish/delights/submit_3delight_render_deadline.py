import os
from dataclasses import dataclass, field, asdict
from datetime import datetime

import pyblish.api

from ayon_core.pipeline import AYONPyblishPluginMixin
from ayon_core.lib import (
    is_in_tests,
    TextDef,
    NumberDef
)
from ayon_deadline import abstract_submit_deadline


@dataclass
class DeadlinePluginInfo:
    SceneFile: str = field(default=None)
    OutputDriver: str = field(default=None)
    Version: str = field(default=None)
    IgnoreInputs: bool = field(default=True)


@dataclass
class DelightsRenderDeadlinePluginInfo:
    InputFile: str = field(default=None)
    Verbose: int = field(default=4)
    SeparateFilesPerFrame: bool = field(default=True)



@dataclass
class RenderDLPluginInfo:
    """Requires RenderDL plugin for Deadline
    """
    SceneFile: str = field()
    # TODO: rewrite to RenderDL
    RenderSettings: str = field(default="/Render/rendersettings")
    Snapshot: int = field(default=-1)
    LogLevel: str = field(default="2")
    Version: str = field(default="")


class HoudiniSubmitDeadline(
    abstract_submit_deadline.AbstractSubmitDeadline,
    AYONPyblishPluginMixin
):
    """Submit Render ROPs to Deadline.

    Renders are submitted to a Deadline Web Service as
    supplied via the environment variable AVALON_DEADLINE.

    Target "local":
        Even though this does *not* render locally this is seen as
        a 'local' submission as it is the regular way of submitting
        a Houdini render locally.

    """

    label = "Submit Render to Deadline"
    order = pyblish.api.IntegratorOrder
    hosts = ["houdini"]
    families = ["3delight_rop"]
    targets = ["local"]
    settings_category = "deadline"

    # presets
    export_priority = 50
    export_chunk_size = 10
    export_group = ""
    export_limits = ""
    export_machine_limit = 0

    @classmethod
    def get_attribute_defs(cls):
        return [
            NumberDef(
                "export_priority",
                label="Export Priority",
                default=cls.export_priority,
                decimals=0
            ),
            NumberDef(
                "export_chunk",
                label="Export Frames Per Task",
                default=cls.export_chunk_size,
                decimals=0,
                minimum=1,
                maximum=1000
            ),
            TextDef(
                "export_group",
                default=cls.export_group,
                label="Export Group Name"
            ),
            TextDef(
                "export_limits",
                default=cls.export_limits,
                label="Export Limit Groups",
                placeholder="value1,value2",
                tooltip="Enter a comma separated list of limit groups."
            ),
            NumberDef(
                "export_machine_limit",
                default=cls.export_machine_limit,
                label="Export Machine Limit",
                tooltip="maximum number of machines for this job."
            ),
        ]

    def get_job_info(self, dependency_job_ids=None, job_info=None):

        instance = self._instance
        context = instance.context

        # Whether Deadline render submission is being split in two
        # (extract + render)
        split_render_job = instance.data.get("splitRender")

        # If there's some dependency job ids we can assume this is a render job
        # and not an export job
        is_export_job = True
        if dependency_job_ids:
            is_export_job = False

        job_type = "[RENDER]"

        plugin = "renderdl"

        job_info.Plugin = plugin

        filepath = context.data["currentFile"]
        filename = os.path.basename(filepath)
        job_info.Name = "{} - {} {}".format(filename, instance.name, job_type)
        job_info.BatchName = filename

        if is_in_tests():
            job_info.BatchName += datetime.now().strftime("%d%m%Y%H%M%S")

        # Deadline requires integers in frame range
        start = instance.data["frameStartHandle"]
        end = instance.data["frameEndHandle"]
        frames = "{start}-{end}x{step}".format(
            start=int(start),
            end=int(end),
            step=int(instance.data["byFrameStep"]),
        )
        job_info.Frames = frames

        # Make sure we make job frame dependent so render tasks pick up a soon
        # as export tasks are done
        if split_render_job and not is_export_job:
            job_info.IsFrameDependent = bool(instance.data.get(
                "splitRenderFrameDependent", True))

        attribute_values = self.get_attr_values_from_data(instance.data)
        if split_render_job and is_export_job:
            job_info.Priority = attribute_values.get(
                "export_priority", self.export_priority
            )
            job_info.ChunkSize = attribute_values.get(
                "export_chunk", self.export_chunk_size
            )
            job_info.Group = attribute_values.get(
                "export_group", self.export_group
            )
            job_info.LimitGroups = attribute_values.get(
                "export_limits", self.export_limits
            )
            job_info.MachineLimit = attribute_values.get(
                "export_machine_limit", self.export_machine_limit
            )

        # TODO change to expectedFiles??
        for i, filepath in enumerate(instance.data["files"]):
            dirname = os.path.dirname(filepath)
            fname = os.path.basename(filepath)
            job_info.OutputDirectory += dirname.replace("\\", "/")
            job_info.OutputFilename += fname

        # Add dependencies if given
        if dependency_job_ids:
            job_info.JobDependencies = dependency_job_ids

        return job_info

    def get_plugin_info(self, job_type=None):
        # Not all hosts can import this module.
        import hou

        instance = self._instance
        context = instance.context

        hou_major_minor = hou.applicationVersionString().rsplit(".", 1)[0]

        # Output driver to render
        if job_type == "render":
            product_type = instance.data.get("productType")
            if product_type == "3delight_rop":
                plugin_info = _get_delights_standalone_plugin_info(
                    InputFile=instance.data["output_nsi_files"]
                )
            elif product_type == "nsi":
                plugin_info = self._get_delights_standalone_plugin_info(
                    instance, hou_major_minor)

            else:
                self.log.error(
                    "Product type '%s' not supported yet to split render job",
                    product_type
                )
                return
        else:
            driver = hou.node(instance.data["instance_node"])
            plugin_info = DeadlinePluginInfo(
                SceneFile=context.data["currentFile"],
                OutputDriver=driver.path(),
                Version=hou_major_minor,
                IgnoreInputs=True
            )

        return asdict(plugin_info)

    def process(self, instance):
        if not instance.data["farm"]:
            self.log.debug("Render on farm is disabled. "
                           "Skipping deadline submission.")
            return

        super(HoudiniSubmitDeadline, self).process(instance)

        # TODO: Avoid the need for this logic here, needed for submit publish
        # Store output dir for unified publisher (filesequence)
        output_dir = os.path.dirname(instance.data["files"][0])
        instance.data["outputDir"] = output_dir

    def _get_delights_standalone_plugin_info(self, instance, hou_major_minor):
        # Not all hosts can import this module.
        import hou

        return RenderDLPluginInfo(
            SceneFile=instance.data["output_nsi_files"],
            Version=hou_major_minor
        )


class HoudiniSubmitDeadline3DelightRender(HoudiniSubmitDeadline):
    label = "Submit Render to Deadline (3Delight)"
    families = ["3delight_rop"]

    def from_published_scene(self, replace_in_path=True):
        # Do not use published workfile paths for 3Delight Render ROP because the
        # Export Job doesn't seem to occur using the published path either, so
        # output paths then do not match the actual rendered paths
        return
