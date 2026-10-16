<?php
header('Content-Type: application/json');
header('Cache-Control: no-store');

$GRFICS = ['simulation', 'plc', 'EWS', 'HMI', 'kali', 'router', 'caldera', 'wazuh'];

function docker_get($path) {
    $sock = @stream_socket_client('unix:///var/run/docker.sock', $errno, $errstr, 3);
    if (!$sock) return null;
    stream_set_timeout($sock, 3);
    fwrite($sock, "GET $path HTTP/1.0\r\nHost: localhost\r\n\r\n");
    $raw = '';
    while (!feof($sock)) $raw .= fread($sock, 8192);
    fclose($sock);
    $sep = strpos($raw, "\r\n\r\n");
    return $sep !== false ? json_decode(substr($raw, $sep + 4), true) : null;
}

$raw = docker_get('/containers/json?all=1');
$docker_available = $raw !== null;
$result = [];

if ($docker_available) {
    foreach ($raw as $c) {
        $name = ltrim($c['Names'][0] ?? '', '/');
        if (!in_array($name, $GRFICS)) continue;
        $labels = $c['Labels'] ?? [];
        $result[$name] = [
            'name'    => $name,
            'version' => $labels['org.opencontainers.image.version'] ?? 'unknown',
            'created' => $labels['org.opencontainers.image.created'] ?? 'unknown',
            'status'  => $c['State'] ?? 'unknown',
        ];
    }
}

// If Docker unavailable, at least show this container's own version from the baked-in file
if (!$docker_available) {
    $local = @file_get_contents('/etc/grfics-release');
    $data  = $local ? json_decode($local, true) : [];
    $result['simulation'] = [
        'name'    => 'simulation',
        'version' => $data['version'] ?? 'unknown',
        'created' => $data['created'] ?? 'unknown',
        'status'  => 'running',
    ];
}

foreach ($GRFICS as $name) {
    if (!isset($result[$name])) {
        $result[$name] = ['name' => $name, 'version' => '—', 'created' => '—', 'status' => 'not found'];
    }
}
ksort($result);

echo json_encode(['containers' => array_values($result), 'docker_available' => $docker_available]);
