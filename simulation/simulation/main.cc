#include <string>
#include <string.h>
#include <iostream>
#include <sstream>
#include <stdlib.h>
#include <sys/types.h> 
#include <arpa/inet.h>    //close 
#include <sys/types.h> 
#include <sys/socket.h> 
#include <netinet/in.h> 
#include <sys/time.h> //FD_SET, FD_ISSET, FD_ZERO macros 
#include <errno.h> 
#include <pthread.h>
#include <jsoncpp/json/json.h>
#include <array>
#include <cmath>
#include <memory>
#include "TE_process.h"
#define PORT 55555
#define TRUE 1

static bool send_frame(int fd, const std::string &frame) {
    size_t sent = 0;
    while (sent < frame.size()) {
        ssize_t n = send(fd, frame.data() + sent, frame.size() - sent, MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        sent += static_cast<size_t>(n);
    }
    return true;
}

static bool valid_inputs(const Json::Value &data) {
    if (!data.isObject() || !data["inputs"].isObject()) return false;
    const auto &in = data["inputs"];
    for (const char *key : {"f1_valve_sp", "f2_valve_sp", "purge_valve_sp", "product_valve_sp"}) {
        if (in.isMember(key) && (!in[key].isNumeric() || !std::isfinite(in[key].asDouble())
                || in[key].asDouble() < 0 || in[key].asDouble() > 100)) return false;
    }
    if (in.isMember("e_stop") && !(in["e_stop"].isBool() ||
            (in["e_stop"].isInt() && (in["e_stop"].asInt() == 0 || in["e_stop"].asInt() == 1)))) return false;
    return true;
}


// compile with g++ example.cc TE_process.cc -ljsoncpp -llapacke -lpthread -o simulation
TE *my_te;
Json::Value inputs;
int main(void) {
    Json::CharReaderBuilder builder;
    builder["failIfExtra"] = true;
    std::unique_ptr<Json::CharReader> reader(builder.newCharReader());
    Json::FastWriter writer;
    Json::Value command;

    struct timeval timeout;
    timeout.tv_sec = 1;      // seconds
    timeout.tv_usec = 0;     // microseconds

    

    int opt = TRUE;  
    int master_socket , addrlen , new_socket , client_socket[30] , 
          max_clients = 30 , activity, i , valread , sd;  
    int max_sd;  
    std::array<std::string, 30> pending;
    struct sockaddr_in address;  
        
    char buffer[1025];  //data buffer of 1K 
        
    //set of socket descriptors 
    fd_set readfds;  
        

    
    //initialize TE process
    my_te = new TE();
    my_te->update(inputs);

    //initialise all client_socket[] to 0 so not checked 
    for (i = 0; i < max_clients; i++)  
    {  
        client_socket[i] = 0;  
    }  
        
    //create a master socket 
    if( (master_socket = socket(AF_INET , SOCK_STREAM , 0)) == 0)  
    {  
        perror("socket failed");  
        exit(EXIT_FAILURE);  
    }  
    
    //set master socket to allow multiple connections , 
    //this is just a good habit, it will work without this 
    if( setsockopt(master_socket, SOL_SOCKET, SO_REUSEADDR, (char *)&opt, 
          sizeof(opt)) < 0 )  
    {  
        perror("setsockopt");  
        exit(EXIT_FAILURE);  
    }  
    
    //type of socket created 
    address.sin_family = AF_INET;  
    address.sin_addr.s_addr = INADDR_ANY;  
    const char *port_env = getenv("SCRAM_SIM_PORT");
    int port = port_env ? atoi(port_env) : PORT;
    if (port < 1 || port > 65535) return 2;
    address.sin_port = htons(port);
        
    //bind the socket to localhost port 55555 
    if (bind(master_socket, (struct sockaddr *)&address, sizeof(address))<0)  
    {  
        perror("bind failed");  
        exit(EXIT_FAILURE);  
    }  
    printf("Listener on port %d \n", PORT);  
        
    //try to specify maximum of 10 pending connections for the master socket 
    if (listen(master_socket, 10) < 0)  
    {  
        perror("listen");  
        exit(EXIT_FAILURE);  
    }  
        
    //accept the incoming connection 
    addrlen = sizeof(address);  
    puts("Waiting for connections ...");  
        
    while(TRUE)  
    {  
        my_te->update(inputs);
        //clear the socket set 
        FD_ZERO(&readfds);  
    
        //add master socket to set 
        FD_SET(master_socket, &readfds);  
        max_sd = master_socket;  
            
        //add child sockets to set 
        for ( i = 0 ; i < max_clients ; i++)  
        {  
            //socket descriptor 
            sd = client_socket[i];  
                
            //if valid socket descriptor then add to read list 
            if(sd > 0)  
                FD_SET( sd , &readfds);  
                
            //highest file descriptor number, need it for the select function 
            if(sd > max_sd)  
                max_sd = sd;  
        }  

        timeout.tv_sec = 1;
        timeout.tv_usec = 100000;
        timeout.tv_sec = 0;
    
        //wait for an activity on one of the sockets
        activity = select( max_sd + 1 , &readfds , NULL , NULL , &timeout);  
      
        if ((activity < 0) && (errno!=EINTR))  
        {  
            printf("select error");  
        }  
            
        //If something happened on the master socket , 
        //then its an incoming connection 
        if (FD_ISSET(master_socket, &readfds))  
        {  
            if ((new_socket = accept(master_socket, 
                    (struct sockaddr *)&address, (socklen_t*)&addrlen))<0)  
            {  
                perror("accept");  
                exit(EXIT_FAILURE);  
            }  
            
            //inform user of socket number - used in send and receive commands 
            printf("New connection , socket fd is %d , ip is : %s , port : %d \n" , new_socket , inet_ntoa(address.sin_addr) , ntohs
                  (address.sin_port));  
          
                
            //add new socket to array of sockets 
            timeval send_timeout = {2, 0};
            setsockopt(new_socket, SOL_SOCKET, SO_SNDTIMEO, &send_timeout, sizeof(send_timeout));
            bool accepted = false;
            for (i = 0; i < max_clients; i++)  
            {  
                //if position is empty 
                if( client_socket[i] == 0 )  
                {  
                    client_socket[i] = new_socket;  
                    pending[i].clear();
                    accepted = true;
                    printf("Adding to list of sockets as %d\n" , i);  
                        
                    break;  
                }  
            }  
            if (!accepted) close(new_socket);
        }  
            
        //else its some IO operation on some other socket
        for (i = 0; i < max_clients; i++)  
        {  
            sd = client_socket[i];  
            if (sd <= 0) continue;
                
            if (FD_ISSET( sd , &readfds))  
            {  
                //Check if it was for closing , and also read the 
                //incoming message 
                if ((valread = read( sd , buffer, 1024)) <= 0)
                {  
                    //Somebody disconnected , get his details and print 
                    getpeername(sd , (struct sockaddr*)&address , \
                        (socklen_t*)&addrlen);  
                    printf("Host disconnected , ip %s , port %d \n" , inet_ntoa(address.sin_addr) , ntohs(address.sin_port));  
                        
                    //Close the socket and mark as 0 in list for reuse 
                    close( sd );  
                    client_socket[i] = 0;  
                    pending[i].clear();
                }   
                    
                    // PROCESS INPUTS OR OUTPUTS
                else
                {  
                    pending[i].append(buffer, static_cast<size_t>(valread));
                    if (pending[i].size() > 65536) {
                        close(sd); client_socket[i] = 0; pending[i].clear(); continue;
                    }
                    size_t newline;
                    while ((newline = pending[i].find('\n')) != std::string::npos) {
                        std::string frame = pending[i].substr(0, newline);
                        pending[i].erase(0, newline + 1);
                        Json::Value response;
                        command.clear();
                        std::string parse_error;
                        if (!reader->parse(frame.data(), frame.data() + frame.size(), &command, &parse_error)
                                || !command.isObject() || !command["request"].isString()) {
                            response["error"] = "invalid JSON request";
                        } else if (command["request"] == "read") {
                            response = my_te->get_state_json();
                        } else if (command["request"] == "write" && valid_inputs(command["data"])) {
                            inputs = command["data"];
                            my_te->update(inputs);
                            response = my_te->get_state_json();
                        } else {
                            response["error"] = "unknown request or invalid inputs";
                        }
                        if (!send_frame(sd, writer.write(response))) {
                            close(sd); client_socket[i] = 0; pending[i].clear(); break;
                        }
                    }
                }  
            }  
        }  
    }

    return 0;
}
