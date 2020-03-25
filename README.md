# RTMP stream monitor

Small utility to monitor certain aspects of RTMP streaming
for debugging purposes.

## How to run

Build the images for the nginx RTMP server and the image
for the webserver that displays the data:
```shell
docker build -t rtmp_server -f Dockerfile.rtmp .
docker build -t rtmp_display -f Dockerfile.display .
```

Create a network to share between the servers:
```shell
docker network create rtmp
```

Run the RTMP server:
```shell
docker run --name rtmp_server --network rtmp -it --rm -p 8080:80 -p 1935:1935 rtmp_server
```

Run the monitoring weserver:
```shell
docker run --name rtmp_mon --network rtmp -e 'STAT_URL=http://rtmp_server/stat' -p 8081:8081 -it --rm rtmp_display
```

Now you can go in your browser to localhost:8081/display to watch the data.
If the graph continously show 103, it means that there is no live
stream.

Go to OBS, create a new stream targeted to 'rtmp://localhost/live'
