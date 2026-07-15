import serial
import threading
from tkinter import Tk, Label, Button, Entry

ser = serial.Serial('COM5', 9600, timeout=1)
continuePlotting = False
tempC = []
time = []
sp = []
cnt = 0
temp = 0

ser.write("<0,10,0,0,0,0>".encode())

class App(threading.Thread):

    def __init__(self, master):
        self.root = master
        # threading.Thread.__init__(self)
        # self.start()
        master.title("PID Temperature Controller")

        self.label = Label(master, text="Controls:").grid(row=0)
        self.label = Label(master, text="Current Temperature:").grid(row=7, column=0, sticky="W")

        self.label = Label(master, text='Set Temperature').grid(row=1)
        self.label = Label(master, text='Ramping Rate').grid(row=2)
        self.label = Label(master, text='P Term').grid(row=3)
        self.label = Label(master, text='I Term').grid(row=4)
        self.label = Label(master, text='D Term').grid(row=5)
        self.label = Label(master, text='Temperature Offset').grid(row=6)

        e1 = Entry(master)
        e2 = Entry(master)
        e3 = Entry(master)
        e4 = Entry(master)
        e5 = Entry(master)
        e6 = Entry(master)
        e1.grid(row=1, column=1)
        e2.grid(row=2, column=1)
        e3.grid(row=3, column=1)
        e4.grid(row=4, column=1)
        e5.grid(row=5, column=1)
        e6.grid(row=6, column=1)
        e1.insert(0, 0)
        e2.insert(0, 10)
        e3.insert(0, 1.0)
        e4.insert(0, 0.6)
        e5.insert(0, 0)
        e6.insert(0, 0)

        def show_entry_fields():
            print("Setpoint=%s C\nRamping Rate=%s s/C\nP Term=%s\nI Term=%s\nD Term=%s\nOffset=%s" % (e1.get(), e2.get(), e3.get(), e4.get(), e5.get(), e6.get()))
            InputString = "<%s,%s,%s,%s,%s,%s>" % (e1.get(), e2.get(), e3.get(), e4.get(), e5.get(), e6.get())
            #print(InputString)
            ser.write(InputString.encode())

        def getdata():
            while continuePlotting:
                root.update()

                global cnt
                global time
                global tempC
                global sp
                while ser.inWaiting() == 0:  # Wait here until there is data
                    pass  # do nothing
                arduinoString = ser.readline()  # read the line of text from the serial port
                dataArray = arduinoString.split(','.encode())  # Split it into an array called dataArray
                global temp
                t = float(dataArray[0])  # Convert first element to floating number and put in t
                temp = float(dataArray[1])  # Convert second element to floating number and put in temp
                setpoint = float(dataArray[2])
                tempC.append(temp)  # Build our tempC array by appending temp readings
                time.append(t)  # Building our time array by appending t readings
                sp.append(setpoint)
                cnt = cnt + 1
                if cnt > 200:  # If you have 200 or more points, delete the first one from the array
                    tempC.pop(0)  # This allows us to just see the last 200 data points
                    time.pop(0)
                    sp.pop(0)
                t1 = Label(master, text="%s" % temp)
                t1.grid(row=7, column=1, sticky="E")

        def stopplot():
            global continuePlotting
            if continuePlotting:
                continuePlotting = False
            master.quit()

        self.button = Button(master, text='Quit', command=stopplot).grid(row=7, column=1)
        self.button = Button(master, text='Enter', command=show_entry_fields).grid(row=7, column=1, sticky='W')

        global continuePlotting
        continuePlotting = True
        getdata()


root = Tk()
my_gui = App(root)
# root.mainloop()

ser.write("<0,10,0,0,0,0>".encode())
