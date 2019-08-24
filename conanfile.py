from conans import ConanFile, tools
from conans.errors import ConanException
from io import StringIO
import os, sys, platform, re, shutil


class TensorFlowLiteConan(ConanFile):
    name = "tensorflow-lite"
    version = "1.14.0"
    description = "The core open source library to help you develop and train ML models"
    url = "https://github.com/bincrafters/conan-tensorflow"
    homepage = "https://www.tensorflow.org/lite"
    author = "Douman <douman@gmx.se>"
    license = "Apache-2.0"
    source_subfolder = "source_subfolder"
    sycl_path = "triSYCL-master"
    # 1.14.0 requires bazel 0.24.1
    # wget https://github.com/bazelbuild/bazel/releases/download/0.24.1/bazel-0.24.1-installer-darwin-x86_64.sh
    bazel_version = "0.24.1"
    generators = "cmake"
    settings = {
        "compiler": None,
        "os": None,
        "arch": ["x86_64", "x86", "armv7", "armv8"],
    }
    options = {
        "gpu": [True, False],
    }
    default_options = {"gpu": False}
    ndk_path = None

    def config_options(self):
        pass
        #if self.settings.os == 'Windows':
        #    del self.options.fPIC

    def source(self):
        system = platform.system().lower()
        is_windows = system == "windows"
        if is_windows:
            bazel_name = "bazel-{}-windows-x86_64.exe".format(self.bazel_version)
        else:
            bazel_name = "bazel-{}-installer-{}-x86_64.sh".format(self.bazel_version, system)

        if not os.path.exists(bazel_name):
            self.output.info("Downloading {}".format(bazel_name))
            if is_windows:
                tools.download("https://github.com/bazelbuild/bazel/releases/download/0.24.1/{}".format(bazel_name), filename=bazel_name)
            else:
                tools.get("https://github.com/bazelbuild/bazel/releases/download/0.24.1/{}".format(bazel_name))

        if not os.path.exists(self.sycl_path):
            self.output.info("Downloading triSYCL...")
            tools.get("https://github.com/triSYCL/triSYCL/archive/master.zip")

        if not os.path.exists(self.source_subfolder):
            source_url = "https://github.com/tensorflow/tensorflow/archive/v{}.tar.gz".format(self.version)
            self.output.info("Downloading sources {}".format(source_url))
            tools.get(source_url)

            extracted_dir = "tensorflow-" + self.version
            os.rename(extracted_dir, self.source_subfolder)

    def fix_android_bzl(self):
        """
        Fixes android.bzl for build in current directory
        Does nothing for builds without NDK
        Should be unneded once https://github.com/tensorflow/tensorflow/pull/31918 is merged
        """
        if self.ndk_path is None:
            return

        stdout = StringIO()
        try:
            self.run("bazel info", output=stdout)
        except ConanException as err:
            android_error = re.search("ERROR: ([^:]+):[0-9]+:[0-9]+: indentation error", stdout.getvalue(), flags=re.MULTILINE)
            # We should not fail here other than due to bad android.bzl
            if android_error is None:
                raise err

            android_bzl = android_error.group(1)
            with open(android_bzl, 'r') as bzl_orig:
                android_bzl_lines = bzl_orig.readlines()
            with open(android_bzl, 'w') as bzl_orig:
                for line in android_bzl_lines:
                    if not " pass\n" in line:
                        bzl_orig.write(line)

            self.output.info(">>>Fixing {}".format(android_bzl))
            self.run("pip3 install --upgrade autopep8")
            self.run("autopep8 --in-place {}".format(android_bzl))


    def build(self):
        if self.settings.arch in ("armv7", "armv8"):
            self.ndk_path = self.env_info.vars.get("ANDROID_NDK")

        sycl_path = os.path.abspath(self.sycl_path)

        # tensorflow fixed on master https://github.com/tensorflow/tensorflow/commit/b77b28d9db08a5f29988e7ca5e628df2b168d433#diff-53f8512109b5194fdae1e37b9018d0fa
        # Remove on next release
        try:
            self.run("patch -s -f {}/tensorflow/lite/delegates/gpu/gl/egl_context.cc < egl_context.patch".format(self.source_subfolder))
        except:
            # Already applied
            pass

        with tools.chdir(self.source_subfolder):
            env_build = dict()
            env_build["PYTHON_BIN_PATH"] = sys.executable
            env_build["USE_DEFAULT_PYTHON_LIB_PATH"] = "1"
            env_build["TF_ENABLE_XLA"] = '0'
            env_build["TF_NEED_OPENCL_SYCL"] = '0'
            env_build["TF_NEED_ROCM"] = '0'
            env_build["TF_NEED_CUDA"] = '0'
            env_build["TF_NEED_MPI"] = '0'
            env_build["TF_DOWNLOAD_CLANG"] = '0'
            env_build["TF_SET_ANDROID_WORKSPACE"] = "0"
            # Avoid configure.py prompt
            env_build["TF_CONFIGURE_IOS"] = "1" if self.settings.os == "iOS" else "0"

            if self.settings.arch == 'armv7':
                extra_flags = '--config=android_arm'
            elif self.settings.arch == 'armv8':
                extra_flags = '--config=android_arm64'
            elif self.settings.compiler != "Visual Studio":
                #Host
                extra_flags = "--linkopt='-latomic'"
            else:
                extra_flags = ''

            if self.ndk_path is not None:
                self.output.info("Using NDK: {}".format(self.ndk_path))
                env_build["ANDROID_NDK_HOME"] = self.ndk_path

                #configure.py should be able to get list of all API levels
                #but in this case it will prompt us to select, which is not desirable for automation
                #We choose current highest supported 18, which is also the one that allows GPU acc.
                env_build["ANDROID_NDK_API_LEVEL"] = '18'

                #On Android our option can be only OpenCL
                #Requires OpenGL ES 3.1 at least
                env_build["TF_NEED_OPENCL_SYCL"] = '1'

                #ComputeCPP is better supported, but TriSYCL is more open
                #Both are external dependencies
                env_build["TF_NEED_COMPUTECPP"] = '0'
                env_build["TRISYCL_INCLUDE_DIR"] = "{}/include".format(sycl_path)
                if self.settings.compiler == "clang":
                    env_build["HOST_CXX_COMPILER"] = "{}/toolchains/llvm/prebuilt/linux-x86_64/bin/clang++".format(self.ndk_path)
                    env_build["HOST_C_COMPILER"] = "{}/toolchains/llvm/prebuilt/linux-x86_64/bin/clang".format(self.ndk_path)
                else:
                    env_build["HOST_CXX_COMPILER"] = "{}/toolchains/llvm/prebuilt/linux-x86_64/bin/g++".format(self.ndk_path)
                    env_build["HOST_C_COMPILER"] = "{}/toolchains/llvm/prebuilt/linux-x86_64/bin/gcc".format(self.ndk_path)

                # NDK compiler doesn't support -march=native?
                env_build["CC_OPT_FLAGS"] = '-Wno-sign-compare'

            else:
                # Necessary work-around to compile GPU delegate
                if self.options.gpu:
                    extra_flags = extra_flags + " --cxxopt='-DMESA_EGL_NO_X11_HEADERS' "

                if self.settings.compiler == "clang":
                    env_build["HOST_CXX_COMPILER"] = "clang++"
                    env_build["HOST_C_COMPILER"] = "clang"
                else:
                    env_build["HOST_CXX_COMPILER"] = "g++"
                    env_build["HOST_C_COMPILER"] = "gcc"

                env_build["CC_OPT_FLAGS"] = "/arch:AVX" if self.settings.compiler == "Visual Studio" else "-Wno-sign-compare -march=native"

            with tools.environment_append(env_build):
                self.run("python configure.py")
                if self.ndk_path is not None:
                    with open('.tf_configure.bazelrc', 'a') as rc:
                        rc.write("build --action_env ANDROID_NDK_HOME=\"{}\"\n".format(self.ndk_path))
                        rc.write("build --action_env ANDROID_NDK_API_LEVEL=\"18\"\n")

                self.run("bazel shutdown")

                self.fix_android_bzl()

                #First library will contain all tensorflowlite APIs
                #The second is GPU delegate library for the tensorflowlite
                #Note that GPU delegate is only tested on Android/IOS
                targets = [
                    "//tensorflow/lite:libtensorflowlite.so",
                ]

                if self.options.gpu:
                    targets.append("//tensorflow/lite/delegates/gpu:libtensorflowlite_gpu_gl.so")

                build_opts = "--config=opt --config=v2 --cxxopt='--std=c++11' --define=no_tensorflow_py_deps=true {}".format(extra_flags)
                cmd = "bazel build {} {} --verbose_failures".format(build_opts, " ".join(targets))
                self.output.info(">>>{}".format(cmd))
                self.run(cmd)

    def package(self):
        lib_dir = "{}/bazel-bin/tensorflow/lite/".format(self.source_subfolder)
        inc_dir = "{}/tensorflow/lite/".format(self.source_subfolder)

        # Work-around to not fail copy below, as conan cannot handle multiple files with the same name
        # and fails with PermissionError
        shutil.rmtree("{}/libtensorflowlite.so.runfiles".format(lib_dir), True)
        shutil.rmtree("{}/delegates/gpu/libtensorflowlite_gpu_gl.so.runfiles".format(lib_dir), True)

        self.copy("*.so", dst="lib", src=lib_dir, keep_path=False, symlinks=None)
        self.copy("*.dll", dst="lib", src=lib_dir, keep_path=False, symlinks=None)
        self.copy("*.dylib*", dst="lib", src=lib_dir, keep_path=False, symlinks=None)

        self.copy("*.h", dst="inc", src=inc_dir, keep_path=True, symlinks=True)
        self.copy("*.hpp", dst="inc", src=inc_dir, keep_path=True, symlinks=True)

    def package_info(self):
        self.cpp_info.libs = ["tensorflowlite"]
        if self.options.gpu:
            self.cpp_info.libs.append("tensorflowlite_gpu_gl")
