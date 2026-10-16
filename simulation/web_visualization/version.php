<?php
header('Content-Type: application/json');
header('Cache-Control: no-store');
$data = @file_get_contents('/etc/grfics-release');
echo ($data !== false) ? $data : '{"version":"unknown","created":"unknown"}';
