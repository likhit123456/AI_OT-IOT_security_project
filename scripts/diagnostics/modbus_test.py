from pymodbus.client.sync import ModbusTcpClient

client = ModbusTcpClient("127.0.0.1", port=502)

if client.connect():
    print("[+] Connected to OpenPLC")

    rr = client.read_holding_registers(0, 5, unit=1)

    if rr.isError():
        print("Modbus Error:", rr)
    else:
        print("Registers:", rr.registers)

    client.close()
else:
    print("Connection failed")
