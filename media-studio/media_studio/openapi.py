"""OpenAPI description of the Media Studio HTTP API.

The shared tool registry (``content/config/tools.json``) points its
``media-studio`` entry at ``GET /openapi.json`` so the Content Bot, the Smart
Router, and n8n can build tool definitions from one document. The description
is static: it lists the routes and shapes this service actually serves, and
holds no credentials.
"""

from __future__ import annotations

from media_studio import __version__

_JOB_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "driver": {"type": "string"},
        "prompt": {"type": "string"},
        "params": {"type": "object", "additionalProperties": True},
        "status": {"type": "string", "enum": ["queued", "running", "done", "error", "cancelled"]},
        "error": {"type": "string"},
        "artifacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string"},
                    "size": {"type": "integer"},
                },
            },
        },
        "log_tail": {"type": "string"},
    },
}

_JOB_RESPONSE = {
    "description": "One job record.",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"job": _JOB_SCHEMA},
            }
        }
    },
}

_ERROR_RESPONSE = {
    "description": "The request was rejected.",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"error": {"type": "string"}},
            }
        }
    },
}


def openapi_document() -> dict:
    """Return the OpenAPI document for this service."""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Media Studio",
            "version": __version__,
            "description": (
                "Job API for image and video generation plus uploaded-clip "
                "editing. Drivers: api-image, flow-video, gemini-image, "
                "video-edit."
            ),
        },
        "servers": [{"url": "/"}],
        "security": [{"bearerAuth": []}],
        "paths": {
            "/healthz": {
                "get": {
                    "summary": "Liveness and job counters",
                    "security": [],
                    "responses": {"200": {"description": "Service counters."}},
                }
            },
            "/session/info": {
                "get": {
                    "summary": "Enabled drivers and browser session mode",
                    "responses": {"200": {"description": "Session details."}},
                }
            },
            "/session/probe": {
                "post": {
                    "summary": "Capture a calibration snapshot of a browser driver page",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "driver": {"type": "string"},
                                        "wait_seconds": {"type": "integer"},
                                    },
                                    "required": ["driver"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "202": _JOB_RESPONSE,
                        "400": _ERROR_RESPONSE,
                    },
                }
            },
            "/jobs": {
                "get": {
                    "summary": "List recent jobs",
                    "responses": {"200": {"description": "Recent job records."}},
                },
                "post": {
                    "summary": "Submit one media job",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "driver": {
                                            "type": "string",
                                            "enum": [
                                                "api-image",
                                                "flow-video",
                                                "gemini-image",
                                                "video-edit",
                                            ],
                                        },
                                        "prompt": {"type": "string"},
                                        "params": {
                                            "type": "object",
                                            "additionalProperties": True,
                                            "description": (
                                                "Driver options. video-edit "
                                                "takes {\"upload_id\": \"...\"}."
                                            ),
                                        },
                                    },
                                    "required": ["driver", "prompt"],
                                }
                            }
                        },
                    },
                    "responses": {"202": _JOB_RESPONSE, "400": _ERROR_RESPONSE},
                },
            },
            "/jobs/{id}": {
                "get": {
                    "summary": "Fetch one job with its log tail",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": _JOB_RESPONSE, "404": _ERROR_RESPONSE},
                },
                "delete": {
                    "summary": "Cancel a queued job",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": _JOB_RESPONSE, "404": _ERROR_RESPONSE},
                },
            },
            "/artifacts/{id}/{name}": {
                "get": {
                    "summary": "Download one produced file",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "name", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Raw artifact bytes.",
                            "content": {"application/octet-stream": {}},
                        },
                        "404": _ERROR_RESPONSE,
                    },
                }
            },
            "/brand": {
                "post": {
                    "summary": "Draw the configured brand chip on one image",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/octet-stream": {
                                "schema": {"type": "string", "format": "binary"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "The branded image.",
                            "content": {"application/octet-stream": {}},
                        },
                        "413": _ERROR_RESPONSE,
                    },
                }
            },
            "/uploads": {
                "post": {
                    "summary": "Store one raw video clip for the video-edit driver",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "video/mp4": {
                                "schema": {"type": "string", "format": "binary"}
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "The stored upload.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "upload": {
                                                "type": "object",
                                                "properties": {
                                                    "id": {"type": "string"},
                                                    "size": {"type": "integer"},
                                                    "content_type": {"type": "string"},
                                                },
                                            }
                                        },
                                    }
                                }
                            },
                        },
                        "413": _ERROR_RESPONSE,
                        "415": _ERROR_RESPONSE,
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Set MEDIA_STUDIO_API_TOKEN to require this header.",
                }
            }
        },
    }
