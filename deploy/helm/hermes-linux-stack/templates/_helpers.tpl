{{- define "hermes.name" -}}hermes-smart-router{{- end -}}
{{- define "hermes.labels" -}}
app.kubernetes.io/name: {{ include "hermes.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "hermes.component" -}}
{{- printf "%s-%s" (include "hermes.name" .root) .name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "hermes.componentLabels" -}}
app.kubernetes.io/name: {{ include "hermes.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
app.kubernetes.io/component: {{ .name }}
{{- end -}}
{{- define "hermes.upstreamBaseUrl" -}}
{{- if .Values.upstream.baseUrl -}}
{{- .Values.upstream.baseUrl -}}
{{- else if .Values.upstreamServer.enabled -}}
{{- printf "http://%s:%v/v1" (include "hermes.component" (dict "root" . "name" "upstream")) (ternary .Values.upstreamServer.omniRoute.apiPort .Values.upstreamServer.nineRouter.port (eq .Values.upstreamServer.backend "omniroute")) -}}
{{- else -}}
http://nine-router:20128/v1
{{- end -}}
{{- end -}}
{{- define "hermes.upstreamHealthUrl" -}}
{{- if .Values.upstream.healthUrl -}}
{{- .Values.upstream.healthUrl -}}
{{- else if .Values.upstreamServer.enabled -}}
{{- if eq .Values.upstreamServer.backend "omniroute" -}}
{{- printf "http://%s:%v/api/monitoring/health" (include "hermes.component" (dict "root" . "name" "upstream")) .Values.upstreamServer.omniRoute.apiPort -}}
{{- else -}}
{{- printf "http://%s:%v/api/health" (include "hermes.component" (dict "root" . "name" "upstream")) .Values.upstreamServer.nineRouter.port -}}
{{- end -}}
{{- else -}}
http://nine-router:20128/api/health
{{- end -}}
{{- end -}}
{{- define "hermes.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}
