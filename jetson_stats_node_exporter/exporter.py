from prometheus_client.core import GaugeMetricFamily
from .logger import factory
from .jtop_stats import JtopObservable
import re
import os


class Jetson(object):
    def __init__(self, update_period=1, enable_tegrastats=False):

        if float(update_period) < 0.5:
            raise BlockingIOError("Jetson Stats only works with 0.5s monitoring intervals and slower.")

        self.jtop_observer = JtopObservable(update_period=update_period)
        self.jtop_stats = {}
        self.disk = {}
        self.disk_units = "GB"
        self.interval = update_period
        self.video_engine_utilization = {}
        self.enable_tegrastats = enable_tegrastats

    def update(self):
        self.jtop_stats = self.jtop_observer.read_stats()
        self.disk, self.disk_units = self.jtop_observer.get_storage_info()
        if self.enable_tegrastats:
            self._parse_video_engine_utilization()

    def _parse_video_engine_utilization(self):
        """Parse tegrastats output for NVENC, NVDEC, NVJPG utilization percentages"""
        tegrastats_log = '/var/log/tegrastats/tegrastats.log'

        try:
            # Check if log file exists
            if not os.path.exists(tegrastats_log):
                self.video_engine_utilization = {}
                return

            # Read the last line from tegrastats log file
            with open(tegrastats_log, 'r') as f:
                # Read last non-empty line
                lines = f.readlines()
                if not lines:
                    self.video_engine_utilization = {}
                    return

                # Get the most recent line (last line)
                line = lines[-1].strip()
                if not line:
                    self.video_engine_utilization = {}
                    return

            # Parse NVENC, NVDEC, NVJPG percentages
            # Pattern matches: NVENC 45%@793, NVDEC 20%@857, NVJPG 10%@729, etc.
            pattern = r'(NVENC|NVDEC|NVJPG\d*)\s+(\d+)%'

            self.video_engine_utilization = {}
            for match in re.finditer(pattern, line):
                engine_name = match.group(1)
                utilization = int(match.group(2))
                self.video_engine_utilization[engine_name] = utilization

        except (IOError, FileNotFoundError):
            # If file doesn't exist or can't be read, leave utilization empty
            self.video_engine_utilization = {}
        except Exception:
            # Catch any other errors silently
            self.video_engine_utilization = {}


class JetsonExporter(object):

    def __init__(self, update_period, enable_tegrastats=False):
        self.jetson = Jetson(update_period, enable_tegrastats=enable_tegrastats)
        self.logger = factory(__name__)
        self.name = "Jetson"
        self.enable_tegrastats = enable_tegrastats

    def __cpu(self):
        cpu_gauge = GaugeMetricFamily(
            name="cpu",
            documentation="CPU Statistics from Jetson Stats (ARMv8 Processor rev 1 (v8l))",
            labels=["core", "statistic"],
            unit="Hz"
        )

        for core_number, core_data in enumerate(self.jetson.jtop_stats["cpu"]["cpu"]):
            if core_data["online"]:
                cpu_gauge.add_metric([str(core_number), "freq"], value=core_data["freq"]["cur"])
                cpu_gauge.add_metric([str(core_number), "min_freq"], value=core_data["freq"]["min"])
                cpu_gauge.add_metric([str(core_number), "max_freq"], value=core_data["freq"]["max"])
                cpu_gauge.add_metric([str(core_number), "val"], value=core_data["idle"])
        return cpu_gauge

    def __gpu(self):
        gpu_gauge = GaugeMetricFamily(
            name="gpu_utilization_percentage",
            documentation="GPU Statistics from Jetson Stats",
            labels=["statistic", "nvidia_gpu"],
            unit="Hz"
        )

        gpu_names = self.jetson.jtop_stats["gpu"].keys()

        for gpu_name in gpu_names:
            gpu_gauge.add_metric([gpu_name, "freq"], value=self.jetson.jtop_stats["gpu"][gpu_name]["freq"]["cur"])
            gpu_gauge.add_metric([gpu_name, "min_freq"], value=self.jetson.jtop_stats["gpu"][gpu_name]["freq"]["min"])
            gpu_gauge.add_metric([gpu_name, "max_freq"], value=self.jetson.jtop_stats["gpu"][gpu_name]["freq"]["max"])

        return gpu_gauge

    def __gpu_load(self):
        gpu_load_gauge = GaugeMetricFamily(
            name="gpu_load_percentage",
            documentation="GPU Load/Utilization Percentage from Jetson Stats",
            labels=["nvidia_gpu"]
        )

        gpu_names = self.jetson.jtop_stats["gpu"].keys()

        for gpu_name in gpu_names:
            gpu_data = self.jetson.jtop_stats["gpu"][gpu_name]

            # GPU utilization percentage from status.load
            if "status" in gpu_data and "load" in gpu_data["status"]:
                load = gpu_data["status"]["load"]
                if isinstance(load, (int, float)):
                    gpu_load_gauge.add_metric([gpu_name], value=load)

        return gpu_load_gauge

    def __gpuram(self):
        gpuram_gauge = GaugeMetricFamily(
            name="gpuram",
            documentation=f"Video Memory Statistics from Jetson Stats",
            labels=["statistic", "nvidia_gpu"],
            unit="kB"
        )

        gpu_names = self.jetson.jtop_stats["gpu"].keys()

        for gpu_name in gpu_names:
            gpuram_gauge.add_metric([gpu_name, "mem"], value=self.jetson.jtop_stats["mem"]["RAM"]["shared"])

        return gpuram_gauge

    def __ram(self):
        ram_gauge = GaugeMetricFamily(
            name="ram",
            documentation=f"Memory Statistics from Jetson Stats (unit: kB)",
            labels=["statistic"],
            unit="kB"
        )

        ram_gauge.add_metric(["total"], value=self.jetson.jtop_stats["mem"]["RAM"]["tot"])
        ram_gauge.add_metric(["used"], value=self.jetson.jtop_stats["mem"]["RAM"]["used"])
        ram_gauge.add_metric(["buffers"], value=self.jetson.jtop_stats["mem"]["RAM"]["buffers"])
        ram_gauge.add_metric(["cached"], value=self.jetson.jtop_stats["mem"]["RAM"]["cached"])
        ram_gauge.add_metric(["lfb"], value=self.jetson.jtop_stats["mem"]["RAM"]["lfb"])
        ram_gauge.add_metric(["free"], value=self.jetson.jtop_stats["mem"]["RAM"]["free"])

        return ram_gauge

    def __swap(self):
        swap_gauge = GaugeMetricFamily(
            name="swap",
            documentation=f"Swap Statistics from Jetson Stats",
            labels=["statistic"],
            unit="kB"
        )

        swap_gauge.add_metric(["total"], value=self.jetson.jtop_stats["mem"]["SWAP"]["tot"])
        swap_gauge.add_metric(["used"], value=self.jetson.jtop_stats["mem"]["SWAP"]["used"])
        swap_gauge.add_metric(["cached"], value=self.jetson.jtop_stats["mem"]["SWAP"]["cached"])

        return swap_gauge

    def __emc(self):
        emc_gauge = GaugeMetricFamily(
            name="emc",
            documentation=f"EMC Statistics from Jetson Stats",
            labels=["statistic"],
            unit="Hz"
        )

        emc_gauge.add_metric(["total"], value=self.jetson.jtop_stats["mem"]["EMC"]["cur"])
        emc_gauge.add_metric(["used"], value=self.jetson.jtop_stats["mem"]["EMC"]["max"])
        emc_gauge.add_metric(["cached"], value=self.jetson.jtop_stats["mem"]["EMC"]["min"])

        return emc_gauge

    def __temperature(self):
        temperature_gauge = GaugeMetricFamily(
            name="temperature",
            documentation=f"Temperature Statistics from Jetson Stats (unit: °C)",
            labels=["statistic", "machine_part", "system_critical"],
            unit="C"
        )
        for part, temp in self.jetson.jtop_stats['tmp'].items():
            temperature_gauge.add_metric([part], value=temp["temp"])

        return temperature_gauge

    def __integrated_power_machine_parts(self):
        power_gauge = GaugeMetricFamily(
            name="integrated_power",
            documentation="Power Statistics from internal power sensors (unit: mW/mV/mA)",
            labels=["statistic", "machine_part", "system_critical"]
        )
        if "rail" in self.jetson.jtop_stats["pwr"]:
            for part, reading in self.jetson.jtop_stats["pwr"]["rail"].items():
                power_gauge.add_metric(["voltage", part], value=reading["volt"])
                power_gauge.add_metric(["current", part], value=reading["curr"])
                power_gauge.add_metric(["critical", part], value=reading["warn"])
                power_gauge.add_metric(["power", part], value=reading["power"])
                power_gauge.add_metric(["avg_power", part], value=reading["avg"])

        return power_gauge

    def __integrated_power_total(self):
        power_gauge = GaugeMetricFamily(
            name="integrated_power",
            documentation="Power Statistics from internal power sensors (unit: mW)",
            labels=["statistic", "machine_part", "system_critical"],
            unit="mW"
        )

        if "rail" in self.jetson.jtop_stats["pwr"]:
            power_gauge.add_metric(["power"], value=self.jetson.jtop_stats["pwr"]["tot"]["power"])
            power_gauge.add_metric(["avg_power"], value=self.jetson.jtop_stats["pwr"]["tot"]["avg"])

        return power_gauge

    def __disk(self):
        disk_gauge = GaugeMetricFamily(
            name="disk",
            documentation=f"Local Storage Statistics from Jetson Stats (unit: {self.jetson.disk_units})",
            labels=["mountpoint", "statistic"],
            unit="GB"
        )
        for mountpoint, disk_info in self.jetson.disk.items():
            if mountpoint == "/":
                disk_gauge.add_metric(["total"], value=disk_info["total"])
                disk_gauge.add_metric(["used"], value=disk_info["used"])
                disk_gauge.add_metric(["free"], value=disk_info["free"])
                disk_gauge.add_metric(["percent"], value=disk_info["percent"])

        return disk_gauge

    def __uptime(self):
        uptime_gauge = GaugeMetricFamily(
            name="uptime",
            documentation="Machine Uptime Statistics from Jetson Stats",
            labels=["statistic", "runtime"],
            unit="s"
        )
        uptime_gauge.add_metric(["alive"], value=self.jetson.jtop_stats["upt"].total_seconds())
        return uptime_gauge

    def __nvenc_frequency(self):
        """Export NVENC frequency metrics from jetson-stats"""
        nvenc_freq_gauge = GaugeMetricFamily(
            name="nvenc_frequency",
            documentation="NVENC (Video Encoder) frequency from Jetson Stats",
            labels=["statistic"],
            unit="Hz"
        )

        if "engines" in self.jetson.jtop_stats and "NVENC" in self.jetson.jtop_stats["engines"]:
            engines = self.jetson.jtop_stats["engines"]["NVENC"]
            for engine_name, engine_data in engines.items():
                if isinstance(engine_data, dict):
                    if "cur" in engine_data:
                        nvenc_freq_gauge.add_metric(["freq"], value=engine_data["cur"])
                    if "min" in engine_data:
                        nvenc_freq_gauge.add_metric(["min_freq"], value=engine_data["min"])
                    if "max" in engine_data:
                        nvenc_freq_gauge.add_metric(["max_freq"], value=engine_data["max"])
                    break  # Only export first NVENC

        return nvenc_freq_gauge

    def __nvenc(self):
        """Export NVENC utilization percentage from tegrastats (if enabled)"""
        nvenc_gauge = GaugeMetricFamily(
            name="nvenc_utilization_percentage",
            documentation="NVENC (Video Encoder) utilization percentage from tegrastats",
            labels=["statistic"]
        )

        # Only export if tegrastats is enabled
        if self.enable_tegrastats and "NVENC" in self.jetson.video_engine_utilization:
            value = self.jetson.video_engine_utilization["NVENC"]
            nvenc_gauge.add_metric(["utilization"], value=value)

        return nvenc_gauge

    def __nvdec_frequency(self):
        """Export NVDEC frequency metrics from jetson-stats"""
        nvdec_freq_gauge = GaugeMetricFamily(
            name="nvdec_frequency",
            documentation="NVDEC (Video Decoder) frequency from Jetson Stats",
            labels=["statistic"],
            unit="Hz"
        )

        if "engines" in self.jetson.jtop_stats and "NVDEC" in self.jetson.jtop_stats["engines"]:
            engines = self.jetson.jtop_stats["engines"]["NVDEC"]
            for engine_name, engine_data in engines.items():
                if isinstance(engine_data, dict):
                    if "cur" in engine_data:
                        nvdec_freq_gauge.add_metric(["freq"], value=engine_data["cur"])
                    if "min" in engine_data:
                        nvdec_freq_gauge.add_metric(["min_freq"], value=engine_data["min"])
                    if "max" in engine_data:
                        nvdec_freq_gauge.add_metric(["max_freq"], value=engine_data["max"])
                    break  # Only export first NVDEC

        return nvdec_freq_gauge

    def __nvdec(self):
        """Export NVDEC utilization percentage from tegrastats (if enabled)"""
        nvdec_gauge = GaugeMetricFamily(
            name="nvdec_utilization_percentage",
            documentation="NVDEC (Video Decoder) utilization percentage from tegrastats",
            labels=["statistic"]
        )

        # Only export if tegrastats is enabled
        if self.enable_tegrastats and "NVDEC" in self.jetson.video_engine_utilization:
            value = self.jetson.video_engine_utilization["NVDEC"]
            nvdec_gauge.add_metric(["utilization"], value=value)

        return nvdec_gauge

    def __nvjpg_frequency(self):
        """Export NVJPG frequency metrics from jetson-stats"""
        nvjpg_freq_gauge = GaugeMetricFamily(
            name="nvjpg_frequency",
            documentation="NVJPG (JPEG Encoder/Decoder) frequency from Jetson Stats",
            labels=["engine", "statistic"],
            unit="Hz"
        )

        if "engines" in self.jetson.jtop_stats and "NVJPG" in self.jetson.jtop_stats["engines"]:
            engines = self.jetson.jtop_stats["engines"]["NVJPG"]
            for engine_name, engine_data in engines.items():
                if isinstance(engine_data, dict):
                    if "cur" in engine_data:
                        nvjpg_freq_gauge.add_metric([engine_name.lower(), "freq"], value=engine_data["cur"])
                    if "min" in engine_data:
                        nvjpg_freq_gauge.add_metric([engine_name.lower(), "min_freq"], value=engine_data["min"])
                    if "max" in engine_data:
                        nvjpg_freq_gauge.add_metric([engine_name.lower(), "max_freq"], value=engine_data["max"])

        return nvjpg_freq_gauge

    def __nvjpg(self):
        """Export NVJPG utilization percentage from tegrastats (if enabled)"""
        nvjpg_gauge = GaugeMetricFamily(
            name="nvjpg_utilization_percentage",
            documentation="NVJPG (JPEG Encoder/Decoder) utilization percentage from tegrastats",
            labels=["statistic"]
        )

        # Only export if tegrastats is enabled
        if self.enable_tegrastats:
            # Handles both NVJPG and NVJPG1 (if present)
            for engine_name in ["NVJPG", "NVJPG1"]:
                if engine_name in self.jetson.video_engine_utilization:
                    value = self.jetson.video_engine_utilization[engine_name]
                    nvjpg_gauge.add_metric([engine_name.lower()], value=value)

        return nvjpg_gauge

    def collect(self):
        self.jetson.update()
        yield self.__cpu()
        yield self.__gpu()
        yield self.__gpu_load()
        yield self.__ram()
        yield self.__gpuram()
        yield self.__swap()
        yield self.__emc()
        yield self.__temperature()
        yield self.__integrated_power_machine_parts()
        yield self.__integrated_power_total()
        yield self.__disk()
        yield self.__uptime()
        # Video engine frequency metrics (always exported)
        yield self.__nvenc_frequency()
        yield self.__nvdec_frequency()
        yield self.__nvjpg_frequency()
        # Video engine utilization metrics (only if tegrastats enabled)
        if self.enable_tegrastats:
            yield self.__nvenc()
            yield self.__nvdec()
            yield self.__nvjpg()
