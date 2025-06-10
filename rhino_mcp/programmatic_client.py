import subprocess
import json
import os
import threading
import sys
import logging
from typing import Any, Dict, Optional, IO, List # Added List

logger = logging.getLogger(__name__)
# Configure logger for the client. Users can reconfigure if needed.
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# Assuming this programmatic_client.py is in the outer rhino_mcp folder
# e.g., <project_root>/rhino_mcp/programmatic_client.py

# Directory containing this script (programmatic_client.py) -> <project_root>/rhino_mcp/
CLIENT_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Project root directory (parent of the outer rhino_mcp folder) -> <project_root>/
PROJECT_ROOT_DIR = os.path.abspath(os.path.join(CLIENT_SCRIPT_DIR, ".."))

class MCPClient:
    def __init__(self, python_executable: Optional[str] = None):
        """
        Initializes the MCPClient.

        Args:
            python_executable: Path to the python interpreter to run the MCP server.
                               If None, uses the same interpreter running this script.
                               This interpreter should have the rhino-mcp package installed.
        """
        self.python_executable = python_executable or sys.executable
        self.mcp_server_process: Optional[subprocess.Popen[str]] = None
        self.request_id_counter = 0
        self._lock = threading.Lock() # To ensure thread-safe request_id generation and communication
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None

    def start_server(self) -> None:
        """
        Starts the MCP server as a subprocess.
        The rhino-mcp package (containing rhino_mcp.rhino_mcp.server)
        must be installed in the environment of the python_executable.
        """
        if self.mcp_server_process and self.mcp_server_process.poll() is None:
            logger.info("MCP server is already running.")
            return

        try:
            logger.info(f"Starting MCP server using Python: {self.python_executable}")

            # Command based on pyproject.toml entry point: rhino_mcp.rhino_mcp.server:main
            # This should be runnable if rhino-mcp is installed in the python_executable's env.
            module_to_run = "rhino_mcp.rhino_mcp.server"
            command = [self.python_executable, "-m", module_to_run]

            logger.info(f"Executing command: {' '.join(command)}")
            logger.info(f"Using CWD: {PROJECT_ROOT_DIR}")

            self.mcp_server_process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=PROJECT_ROOT_DIR,
                text=True, # Use text mode for stdin/stdout/stderr
                bufsize=1 # Line-buffered
            )
            logger.info(f"MCP server started with PID: {self.mcp_server_process.pid}")

            if self.mcp_server_process.stdout:
                self._stdout_thread = threading.Thread(target=self._log_stream, args=(self.mcp_server_process.stdout, "MCP_SERVER_STDOUT"), daemon=True)
                self._stdout_thread.start()
            if self.mcp_server_process.stderr:
                self._stderr_thread = threading.Thread(target=self._log_stream, args=(self.mcp_server_process.stderr, "MCP_SERVER_STDERR"), daemon=True)
                self._stderr_thread.start()

        except FileNotFoundError:
            logger.error(f"Python executable not found: {self.python_executable}. Please ensure it's in your PATH or provide a full path.")
            self.mcp_server_process = None # Ensure it's None if startup fails
            raise
        except Exception as e:
            logger.error(f"Failed to start MCP server: {e}")
            if self.mcp_server_process:
                self.mcp_server_process.terminate()
                self.mcp_server_process.wait()
            self.mcp_server_process = None
            raise

    def _log_stream(self, stream: IO[str], prefix: str) -> None:
        """Logs messages from a stream (stdout/stderr) with a prefix."""
        try:
            for line in iter(stream.readline, ''):
                logger.info(f"[{prefix}] {line.strip()}")
        except (IOError, ValueError) as e: # Handle stream closed or other IO issues
            logger.warning(f"Error reading from stream {prefix}: {e}")
        finally:
            try:
                stream.close()
            except IOError: # pragma: no cover
                pass # Stream might already be closed

    def stop_server(self) -> None:
        """
        Stops the MCP server subprocess.
        """
        if self.mcp_server_process and self.mcp_server_process.poll() is None:
            logger.info("Stopping MCP server...")
            try:
                self.mcp_server_process.terminate()
                self.mcp_server_process.wait(timeout=5)
                logger.info("MCP server terminated.")
            except subprocess.TimeoutExpired: # pragma: no cover
                logger.warning("MCP server did not terminate gracefully, killing...")
                self.mcp_server_process.kill()
                self.mcp_server_process.wait()
                logger.info("MCP server killed.")
            except Exception as e: # pragma: no cover
                logger.error(f"Error stopping MCP server: {e}")
            self.mcp_server_process = None
        else:
            logger.info("MCP server is not running or already stopped.")

        # Join logging threads
        if self._stdout_thread and self._stdout_thread.is_alive():
            self._stdout_thread.join(timeout=1)
        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=1)

    def send_command(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Sends a command to the MCP server and returns the response.

        Args:
            method: The name of the tool/method to call (e.g., "execute_rhino_code").
            params: A dictionary of parameters for the method.

        Returns:
            A dictionary containing the server's response.

        Raises:
            RuntimeError: If the server is not running or communication fails.
        """
        if not self.mcp_server_process or self.mcp_server_process.stdin is None or self.mcp_server_process.stdout is None:
            raise RuntimeError("MCP server is not running or streams are unavailable. Call start_server() first.")

        if self.mcp_server_process.poll() is not None:
            logger.error(f"MCP server process has terminated with code {self.mcp_server_process.poll()}. Check server logs.")
            raise RuntimeError(f"MCP server process has terminated. Exit code: {self.mcp_server_process.poll()}.")

        with self._lock:
            self.request_id_counter += 1
            request_id = self.request_id_counter

            request_payload = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
                "id": request_id,
            }

            response_line = ""
            try:
                request_json = json.dumps(request_payload)
                logger.debug(f"Sending to MCP Server: {request_json}")

                self.mcp_server_process.stdin.write(request_json + "\n")
                self.mcp_server_process.stdin.flush()

                response_line = self.mcp_server_process.stdout.readline()
                if not response_line:
                    if self.mcp_server_process.poll() is not None:
                        logger.error(f"MCP Server terminated while awaiting response. Exit code: {self.mcp_server_process.poll()}. Check server logs.")
                        raise RuntimeError(f"No response from MCP server; server terminated. Exit code: {self.mcp_server_process.poll()}.")
                    else:
                        logger.error("Received empty response from MCP server. This might indicate an issue or unexpected EOF.")
                        raise RuntimeError("No response from MCP server (empty line received).")


                logger.debug(f"Received from MCP Server: {response_line.strip()}")
                response_data = json.loads(response_line.strip())

                if response_data.get("id") != request_id: # pragma: no cover
                    logger.warning(f"Received response with mismatched ID. Expected {request_id}, got {response_data.get('id')}")

                if "error" in response_data: # pragma: no cover
                    logger.error(f"MCP Server returned an error: {response_data['error']}")
                    # Consider raising a custom exception e.g. raise MCPError(response_data['error'])

                return response_data

            except BrokenPipeError: # pragma: no cover
                logger.error("Broken pipe: MCP server process may have terminated unexpectedly. Check server logs for details.")
                self.stop_server()
                raise RuntimeError("Communication failed with MCP server (BrokenPipeError). Check server logs.")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode JSON response from MCP server: {e}. Response line: '{response_line.strip()}'")
                raise RuntimeError(f"Invalid JSON response from MCP server: {response_line.strip()}")
            except Exception as e: # pragma: no cover
                logger.error(f"An error occurred while communicating with MCP server: {e}")
                raise RuntimeError(f"An error occurred: {e}")

    # --- Start of new Rhino interaction methods ---

    def execute_rhino_code(self, code: str) -> Dict[str, Any]:
        """
        Execute arbitrary Python code in Rhino.

        Args:
            code: The IronPython 2.7 code string to execute in Rhino.
                  The code should assign its output to a variable named 'result'
                  if a specific return value is expected by the caller, beyond success/failure.
                  The 'add_object_metadata(obj_id, name, description)' function is available in the code's scope.

        Returns:
            The response from the MCP server, typically including status and any result from the code.
        """
        logger.info(f"Executing Rhino code: {code[:100]}{'...' if len(code) > 100 else ''}")
        return self.send_command(method="execute_rhino_code", params={"code": code})

    def get_rhino_objects_with_metadata(self, filters: Optional[Dict[str, Any]] = None,
                                        metadata_fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Get detailed information about objects in the Rhino scene with their metadata.

        Args:
            filters: Optional dictionary of filters to apply (e.g., {"layer": "Default", "name": "Cube*"}).
            metadata_fields: Optional list of specific metadata fields to return. If None, returns default fields.

        Returns:
            The response from the MCP server, containing object details.
        """
        logger.info(f"Getting Rhino objects with metadata. Filters: {filters}, Fields: {metadata_fields}")
        return self.send_command(
            method="get_scene_objects_with_metadata",
            params={"filters": filters or {}, "metadata_fields": metadata_fields}
        )

    def capture_rhino_viewport(self, layer: Optional[str] = None,
                               show_annotations: bool = True, max_size: int = 800) -> Dict[str, Any]:
        """
        Capture the current Rhino viewport as an image.

        Args:
            layer: Optional layer name to filter annotations.
            show_annotations: Whether to show object annotations (short_id).
            max_size: Maximum dimension (width or height) of the captured image.

        Returns:
            The response from the MCP server. If successful, it includes image data (e.g., base64 encoded).
        """
        logger.info(f"Capturing Rhino viewport. Layer: {layer}, Annotations: {show_annotations}, MaxSize: {max_size}")
        return self.send_command(
            method="capture_viewport",
            params={"layer": layer, "show_annotations": show_annotations, "max_size": max_size}
        )

    def get_rhino_scene_info(self) -> Dict[str, Any]:
        """
        Get basic information about the current Rhino scene.

        Returns:
            The response from the MCP server with scene information.
        """
        logger.info("Getting Rhino scene info.")
        return self.send_command(method="get_scene_info")

    def get_rhino_layers(self) -> Dict[str, Any]:
        """
        Get a list of layers in the Rhino document.

        Returns:
            The response from the MCP server with layer information.
        """
        logger.info("Getting Rhino layers.")
        return self.send_command(method="get_layers")

    # --- End of new Rhino interaction methods ---

    # --- Start of new Grasshopper interaction methods ---

    def is_gh_server_available(self) -> Dict[str, Any]:
        """
        Grasshopper: Check if the Grasshopper server is available.
        Returns:
            Server response, typically indicating true if available.
        """
        logger.info("Checking Grasshopper server availability.")
        return self.send_command(method="is_server_available")

    def execute_gh_code(self, code: str) -> Dict[str, Any]:
        """
        Grasshopper: Execute arbitrary Python code in Grasshopper.
        Args:
            code: The IronPython 2.7 code to execute. Use 'result = value' for output.
        Returns:
            Server response with execution result.
        """
        logger.info(f"Executing Grasshopper code: {code[:100]}{'...' if len(code) > 100 else ''}")
        return self.send_command(method="execute_code_in_gh", params={"code": code})

    def get_gh_context(self, simplified: bool = False) -> Dict[str, Any]:
        """
        Grasshopper: Get current Grasshopper document state and definition graph.
        Args:
            simplified: If true, returns minimal component info.
        Returns:
            Server response with Grasshopper definition graph.
        """
        logger.info(f"Getting Grasshopper context. Simplified: {simplified}")
        return self.send_command(method="get_gh_context", params={"simplified": simplified})

    def get_gh_objects(self, instance_guids: List[str], simplified: bool = False, context_depth: int = 0) -> Dict[str, Any]:
        """
        Grasshopper: Get information about specific components by their GUIDs.
        Args:
            instance_guids: List of component GUIDs.
            simplified: If true, returns minimal info.
            context_depth: Levels of connected components to include (0-3).
        Returns:
            Server response with component information.
        """
        logger.info(f"Getting Grasshopper objects. GUIDs: {instance_guids}, Simplified: {simplified}, Depth: {context_depth}")
        return self.send_command(
            method="get_objects",
            params={"instance_guids": instance_guids, "simplified": simplified, "context_depth": context_depth}
        )

    def get_gh_selected(self, simplified: bool = False, context_depth: int = 0) -> Dict[str, Any]:
        """
        Grasshopper: Get information about currently selected components.
        Args:
            simplified: If true, returns minimal info.
            context_depth: Levels of connected components to include (0-3).
        Returns:
            Server response with selected component information.
        """
        logger.info(f"Getting selected Grasshopper components. Simplified: {simplified}, Depth: {context_depth}")
        return self.send_command(
            method="get_selected",
            params={"simplified": simplified, "context_depth": context_depth}
        )

    def update_gh_script(self, instance_guid: str, code: Optional[str] = None,
                         description: Optional[str] = None, message_to_user: Optional[str] = None,
                         param_definitions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Grasshopper: Update a script component.
        Args:
            instance_guid: GUID of the script component.
            code: New Python code.
            description: New description.
            message_to_user: Feedback message.
            param_definitions: List to redefine parameters. If provided, ALL parameters are redefined.
        Returns:
            Server response with update status.
        """
        logger.info(f"Updating Grasshopper script component: {instance_guid}")
        params: Dict[str, Any] = { # Ensure params is typed for clarity
            "instance_guid": instance_guid,
        }
        if code is not None: params["code"] = code
        if description is not None: params["description"] = description
        if message_to_user is not None: params["message_to_user"] = message_to_user
        if param_definitions is not None: params["param_definitions"] = param_definitions

        return self.send_command(method="update_script", params=params)

    def update_gh_script_with_code_reference(self, instance_guid: str, file_path: Optional[str] = None,
                                             param_definitions: Optional[List[Dict[str, Any]]] = None,
                                             description: Optional[str] = None, name: Optional[str] = None,
                                             force_code_reference: bool = False) -> Dict[str, Any]:
        """
        Grasshopper: Update a script component to use code from an external Python file.
        Args:
            instance_guid: GUID of the component.
            file_path: Path to the external Python file.
            param_definitions: Definitions for input/output parameters.
            description: New component description.
            name: New component nickname.
            force_code_reference: If True, sets component to referenced code mode.
        Returns:
            Server response with update status.
        """
        logger.info(f"Updating Grasshopper script component {instance_guid} with code reference: {file_path}")
        params: Dict[str, Any] = {
            "instance_guid": instance_guid,
            "force_code_reference": force_code_reference # force_code_reference is always included
        }
        if file_path is not None: params["file_path"] = file_path
        if param_definitions is not None: params["param_definitions"] = param_definitions
        if description is not None: params["description"] = description
        if name is not None: params["name"] = name

        return self.send_command(method="update_script_with_code_reference", params=params)

    def expire_gh_component(self, instance_guid: str) -> Dict[str, Any]:
        """
        Grasshopper: Expire a specific component and get its updated information.
        Args:
            instance_guid: GUID of the component to expire.
        Returns:
            Server response with component's updated information.
        """
        logger.info(f"Expiring Grasshopper component: {instance_guid}")
        return self.send_command(method="expire_and_get_info", params={"instance_guid": instance_guid})

    # --- End of new Grasshopper interaction methods ---

    def __enter__(self):
        self.start_server()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_server()

if __name__ == "__main__": # pragma: no cover
    logger.info("Starting MCPClient example.")

    client = MCPClient(python_executable=sys.executable)

    try:
        client.start_server()

        logger.info("MCP Server starting... waiting a few seconds for it to initialize.")
        import time
        time.sleep(5)

        if client.mcp_server_process and client.mcp_server_process.poll() is None:
            logger.info("MCP Server process appears to be running.")
            logger.info("The server logs (MCP_SERVER_STDOUT/STDERR) should show connection attempts to Rhino/Grasshopper.")
            logger.info("If Rhino/GH are not running with their respective client scripts, the server will show connection errors.")

            # Example: Try to get Rhino layers (requires Rhino connection to MCP server)
            # try:
            #     logger.info("Attempting to get Rhino layers...")
            #     layers_response = client.get_rhino_layers()
            #     logger.info(f"Get Rhino Layers Response: {json.dumps(layers_response, indent=2)}")
            # except RuntimeError as e:
            #     logger.error(f"Error getting Rhino layers: {e}")
            #     logger.info("This is expected if Rhino is not connected to the MCP server via rhino_mcp_client.py.")

            # Example: Execute a simple code snippet in Rhino
            # try:
            #     logger.info("Attempting to execute simple code in Rhino...")
            #     code_execution_response = client.execute_rhino_code("import rhinoscriptsyntax as rs\nresult = rs.LayerCount()")
            #     logger.info(f"Execute Rhino Code Response: {json.dumps(code_execution_response, indent=2)}")
            # except RuntimeError as e:
            #     logger.error(f"Error executing Rhino code: {e}")
            #     logger.info("This is expected if Rhino is not connected.")

            # Example: Check Grasshopper server availability
            # try:
            #     logger.info("Attempting to check GH server availability...")
            #     gh_status_response = client.is_gh_server_available()
            #     logger.info(f"GH Server Status Response: {json.dumps(gh_status_response, indent=2)}")
            # except RuntimeError as e:
            #     logger.error(f"Error checking GH server status: {e}")
            #     logger.info("This is expected if GH HTTP server (grasshopper_mcp_client.gh) is not running or MCP server cannot reach it.")

            # Example: Get GH Context
            # try:
            #     logger.info("Attempting to get GH context...")
            #     gh_context_response = client.get_gh_context(simplified=True)
            #     logger.info(f"GH Context Response: {json.dumps(gh_context_response, indent=2)}")
            # except RuntimeError as e:
            #     logger.error(f"Error getting GH context: {e}")

            logger.info("Keeping server alive for 5 more seconds to observe logs...")
            time.sleep(5)

        else:
            logger.error("MCP Server process failed to start or terminated prematurely. Check logs above.")

    except Exception as e:
        logger.error(f"An error occurred in the MCPClient example: {e}", exc_info=True)
    finally:
        logger.info("Stopping server from example.")
        client.stop_server()
        logger.info("MCPClient example finished.")
