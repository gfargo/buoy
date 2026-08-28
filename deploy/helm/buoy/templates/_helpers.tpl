{{- define "buoy.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{- define "buoy.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "buoy.imageTag" -}}
{{- .Values.image.tag | default .Chart.AppVersion -}}
{{- end -}}

{{- define "buoy.versionLabel" -}}
{{- $tag := include "buoy.imageTag" . -}}
{{- regexReplaceAll "[^A-Za-z0-9._-]" $tag "-" | trunc 63 | trimAll "._-" | default "unknown" -}}
{{- end -}}

{{- define "buoy.labels" -}}
app.kubernetes.io/name: {{ include "buoy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ include "buoy.versionLabel" . | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "buoy.selectorLabels" -}}
app.kubernetes.io/name: {{ include "buoy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "buoy.authSecretName" -}}
{{- if .Values.auth.existingSecret -}}
{{- .Values.auth.existingSecret -}}
{{- else -}}
{{- include "buoy.fullname" . -}}-auth
{{- end -}}
{{- end -}}
