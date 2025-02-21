#!/usr/bin/env python
#************************************************************
# Copyright (c) MLRS project
# GPL3
# https://www.gnu.org/licenses/gpl-3.0.de.html
# OlliW @ www.olliw.eu
#************************************************************
# Open passthrough to receiver on ArduPilot systems
# 21. Feb. 2025 001
#************************************************************
# Does this:
# - opens serial passthrough in ArduPilot flight controller
# - sets receiver into bootloader mode
#************************************************************

import os, sys, time
import argparse


#--------------------------------------------------
#-- PyMavlink
#--------------------------------------------------

sys.path.append(os.path.join('thirdparty','mavlink'))
from pymavlink import mavutil
from pymavlink import mavparm
os.environ['MAVLINK20'] = '1'
mavutil.set_dialect("all")


#--------------------------------------------------
#-- Tools
#--------------------------------------------------

def find_ardupilot_serial_ports():
    '''
    ArduPilot's USB port is known to have vid == 0x1209, pid = 0x5740
    It usually offers at least two, one SLCAN and one MAVLink
    on Win: 
        description = ArduPilot SLCAN (COMxx) or ArduPilot MAVlink (COMxx)
        manufacturer = ArduPilot Project 
    '''
    try:
        from serial.tools.list_ports import comports
        portList = list(comports())
    except:
        print('ERROR: find_radio_serial_port() [1]')
        return None
    '''    
    for port in portList:
        print('*',port.device)
        print('  ',port.name)
        print('  ',port.description)
        print('  ',port.hwid)
        print('  ',port.vid, port.pid)
        print('  ',port.serial_number)
        print('  ',port.location)
        print('  ',port.manufacturer)
        print('  ',port.product)
        print('  ',port.interface)
    '''    
    apportList = []
    for port in portList:
        if port.vid == 0x1209 and port.pid == 0x5740:
            if 'ardupilot' in port.description.lower() and 'mavlink' in port.description.lower():
                if 'ardupilot' in port.manufacturer.lower():
                    apportList.append(port.device)
    #print(apportList)
    return apportList


def do_msg(msg):
    print(msg)
    print('Press Enter to continue')
    input()


def do_error(msg):
    print(msg)
    print('Press Enter to continue')
    input()
    sys.exit(1)

#find_ardupilot_serial_ports()
#exit()

#--------------------------------------------------
#-- Connect to ArduPilot flight controller via MAVLink
#--------------------------------------------------

# link.recv_match(type = 'PARAM_VALUE', blocking = False, timeout=1.0) seems not to work
def mav_recv_match(link, msg_type, timeout=1.0):
    tstart = time.time()
    msgd = None
    while True:
        time.sleep(0.01)
        msg = link.recv_match(type = msg_type)
        if msg is not None and msg.get_type() == msg_type:
            msgd = msg.to_dict()
            #print(msgd)
            break
        tnow = time.time()
        if tnow - tstart > timeout:
            break
    return msg, msgd


def ardupilot_connect(uart, baud):
    print('connect to flight controller...')
    
    link = mavutil.mavlink_connection(uart, baud)
    if not link:
        return None
    print('  wait for heartbeat...')
    #msg = link.recv_match(type = 'HEARTBEAT', blocking = True)
    #print(' ', msg)
    #print(msg.to_dict())
    msg, msgd = mav_recv_match(link, 'HEARTBEAT', timeout=10.0)
    if not msgd:
        link.close()
        return None
    print(' ', msg)
    msg, msgd = mav_recv_match(link, 'HEARTBEAT', timeout=2.5) # let's wait for a 2nd one to be sure
    if not msgd:
        link.close()
        return None
    # note: link targets appear to always come out as 1,0
    print('  received (sysid %u compid %u)' % (link.target_system, link.target_component))
    print('connected to flight controller')
    return link


def ardupilot_find_serialx_baud(link, serialx):
    print('find SERIALx, receiver baud rate...')
        
    param_str = 'SERIAL'+str(serialx)+'_PROTOCOL'
    link.mav.param_request_read_send(
        link.target_system, link.target_component,
        param_str.encode(), #b'SERIAL1_PROTOCOL',
        -1)
    _, msgd = mav_recv_match(link, 'PARAM_VALUE', timeout=1.0)
    if not msgd or msgd['param_value'] != 2.0:
        link.close()
        return None # something went wrong
    # we do have that SRIALx, and it's MAVLink2
    param_str = 'SERIAL'+str(serialx)+'_BAUD'
    link.mav.param_request_read_send(
        link.target_system, link.target_component,
        param_str.encode(),
        -1)
    _, msgd = mav_recv_match(link, 'PARAM_VALUE', timeout=1.0)
    #print(msgd)
    if not msgd:
        link.close()
        return None # something went wrong
    baud = msgd['param_value']
    if baud == 38:
        baud = 38400
    elif baud == 57:
        baud = 57600
    elif baud == 115:
        baud = 115200
    elif baud == 230:
        baud = 230400
    #print(baud)
    return baud


def ardupilot_open_passthrough(link, serialx, passthru_timeout=0):
    print('open serial passthrough...')
    
    # set up passthrough with no timeout, power cycle to exit
    print('  set SERIAL_PASSTIMO = '+str(passthru_timeout))
    mavparm.MAVParmDict().mavset(link, "SERIAL_PASSTIMO", passthru_timeout)
    print('  set SERIAL_PASS2 =', serialx)
    mavparm.MAVParmDict().mavset(link, "SERIAL_PASS2", serialx)
    time.sleep(1.5) # wait for passthrough to start, AP starts pt after 1 secs, which is so to allow the PARAM_SET to be seen
    print('serial passthrough opened')


#--------------------------------------------------
#-- Verify connection to mLRS receiver
#-- Reboot mLRS receiver
#--------------------------------------------------

# confirmation, send 0 to ping, send 1 to arm, then 2 to execute
def mlrs_cmd_preflight_reboot_shutdown(link, cmd_confirmation, cmd_action, sysid=51, compid=68, tries=0):
    cmd_tlast = 0
    tries_cnt = 0
    while True:
        time.sleep(0.01)

        msg = link.recv_match(type = 'COMMAND_ACK')
        if msg is not None and msg.get_type() != 'BAD_DATA':
            #print(msg)
            msgd = msg.to_dict()
            if (msgd['mavpackettype'] == 'COMMAND_ACK' and
                msgd['command'] == mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN and
                msgd['result_param2'] == 1234321):
                #print('  ACK:', msgd)
                print(' ', msg)
                #print(mavutil.mavlink.enums['MAV_RESULT'][msgd['result']].description)
                return True

        tnow = time.time()
        if tnow - cmd_tlast >= 0.5:
            if tries > 0 and tries_cnt >= tries:
                link.close()
                return False
            cmd_tlast = tnow
            print('  send probe')
            link.mav.command_long_send(
                sysid, compid,
                mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
                cmd_confirmation, # confirmation, send 0 to ping, send 1 to arm, then 2 to execute
                0, 0,
                cmd_action, # param 3: Component action
                68, # param 4: Component ID
                0, 0,
                1234321)
            tries_cnt += 1
            
    link.close()
    return False
            

def mlrs_put_into_systemboot(link, sysid=51, compid=68):
    print('check connection to mLRS receiver...')
    res = mlrs_cmd_preflight_reboot_shutdown(link, 0, 0, sysid=sysid, compid=compid, tries=10)
    if not res:
        do_error('Sorry, something went wrong.')
    print('mLRS receiver connected')
    
    # Arm reboot

    print('arm mLRS receiver for reboot shutdown...')
    res = mlrs_cmd_preflight_reboot_shutdown(link, 1, 3, sysid=sysid, compid=compid, tries=3)
    if not res:
        do_error('Sorry, something went wrong.')
    print('mLRS receiver armed for reboot shutdown')

    # Reboot shutdown

    print('mLRS receiver reboot shutdown...')
    mlrs_cmd_preflight_reboot_shutdown(link, 2, 3, sysid=sysid, compid=compid, tries=3) # probably ok if ot fails
    print('mLRS receiver reboot shutdown DONE')

    print('mLRS receiver jumps to system bootloader in 5 seconds')


def mlrs_find_apport():
    apports_list = find_ardupilot_serial_ports()
    if len(apports_list) != 1:
        do_msg("Please connect the USB to your flight controller.")
        apports_list = find_ardupilot_serial_ports()
        if len(apports_list) != 1:
            do_error('Sorry, something went wrong and we could not find the USB port of your ArduPilot flight controller.')
    apport = apports_list[0]
    print('Your Ardupilot flight controller is on USB port', apport)
    return apport 


def mlrs_find_receiver_baud(apport, serialx, baud):
    link = ardupilot_connect(apport, baud)
    if not link:
        do_error('Sorry, something went wrong.')
    receiver_baud = ardupilot_find_serialx_baud(link, serialx)
    link.close()
    if not receiver_baud:
        do_error('Sorry, something went wrong.')
    print('mLRS receiver baudrate is', receiver_baud)
    return receiver_baud     


def mlrs_open_passthrough(comport, serialx, baud, options=''):
    print('------------------------------------------------------------')
    print('Find USB port of your flight controller')
    if comport:
        apport = comport
    else:    
        apport = mlrs_find_apport()
    print('USB port:', apport)
    print('SERIALx number:', serialx)
    print('Baud rate:', baud)
    print('------------------------------------------------------------')
    link = ardupilot_connect(apport, baud)
    if not link:
        do_error('Sorry, something went wrong.')
    print('------------------------------------------------------------')
    receiver_baud = ardupilot_find_serialx_baud(link, serialx)
    if not receiver_baud:
        do_error('Sorry, something went wrong.')
    if baud != receiver_baud:
        print('Receiver baudrate is ', receiver_baud, ', change link to it')
        link.close()
        link = ardupilot_connect(apport, receiver_baud)
        time.sleep(0.5) # can't hurt
    print('------------------------------------------------------------')
    ardupilot_open_passthrough(link, serialx)
    print('------------------------------------------------------------')
    if not 'nosysboot' in options:
        mlrs_put_into_systemboot(link)
        print('------------------------------------------------------------')
    link.close()
    print('')
    print('PASSTHROUGH READY FOR PROGRAMMING TOOL')
    
    return apport, receiver_baud


#--------------------------------------------------
#-- Main
#--------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description = 'ArduPilot passthrough to mLRS receiver'
        )
    parser.add_argument("-c", "--com", help="Com port corresponding to flight controller USB port. Examples: com5, /dev/ttyACM0")
    parser.add_argument("-s", "--serialx", type=int, help="ArduPilot SERIALx number of the receiver")
    parser.add_argument("-b", "--baud", type=int, default=57600, help="Baudrate of the SERIALx connection to the receiver")
    parser.add_argument("-findport", action='store_true', help = 'Find port')
    parser.add_argument("-findbaud", action='store_true', help = 'Find baudrate')
    parser.add_argument("-nosysboot", action='store_true', help = 'Do not put into system boot')
    args = parser.parse_args()

    comport = args.com
    serialx = args.serialx
    baud = args.baud # must match speed of serial link to receiver, usb baud rate is transparently forwarded to port2 
    
    if args.findport:
        apport = mlrs_find_apport()
        sys.exit(-int(apport[3:])) # report back com port, for use in batch file
    if args.findbaud:
        receiver_baud = mlrs_find_receiver_baud(comport, serialx, baud)
        sys.exit(-receiver_baud) # report back com port, for use in batch file

    options = ''
    if args.nosysboot: 
        options = 'nosysboot'
    mlrs_open_passthrough(comport, serialx, baud, options)
    
    

    
'''
example usage in batch file

@apInitPassthru.py -findport
@if %ERRORLEVEL% GEQ 1 EXIT /B 1
@if %ERRORLEVEL% LEQ 0 set /a PORT=-%ERRORLEVEL%
@apInitPassthru.py -findbaud -c "COM%PORT%" -s 2 
@if %ERRORLEVEL% GEQ 1 EXIT /B 1
@if %ERRORLEVEL% LEQ 0 set /a BAUDRATE=-%ERRORLEVEL%
@apInitPassthru.py -c "COM%PORT%" -s 2 -b %BAUDRATE%  
@if %ERRORLEVEL% GEQ 1 EXIT /B 1
@timeout /t 5 /nobreak
@thirdparty\STM32CubeProgrammer\win\bin\STM32_Programmer_CLI.exe -c port="COM%PORT%" br=%BAUDRATE% -w "temp\rx-R9MX-l433cb-v1.3.05-@28fe6be0.hex" -v -g
@ECHO.
@ECHO *** DONE ***
@ECHO.
@ECHO Cheers, and have fun.

'''